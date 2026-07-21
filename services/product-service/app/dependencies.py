from __future__ import annotations

import logging
import uuid
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from redis.asyncio import Redis

from app.config import get_settings
from app.services.embedding_service import EmbeddingService
from app.services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)
settings = get_settings()

_bearer = HTTPBearer(auto_error=True)

# ---------------------------------------------------------------------------
# Singletons — initialised in lifespan, exposed via dependency functions
# ---------------------------------------------------------------------------

_redis_pool: Optional[Redis] = None
_embedding_service: Optional[EmbeddingService] = None
_qdrant_service: Optional[QdrantService] = None


def set_redis(pool: Redis) -> None:
    global _redis_pool
    _redis_pool = pool


def set_embedding_service(svc: EmbeddingService) -> None:
    global _embedding_service
    _embedding_service = svc


def set_qdrant_service(svc: QdrantService) -> None:
    global _qdrant_service
    _qdrant_service = svc


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_redis() -> Redis:
    if _redis_pool is None:  # pragma: no cover
        raise RuntimeError("Redis pool has not been initialised.")
    return _redis_pool


async def get_embedding_service() -> EmbeddingService:
    if _embedding_service is None:  # pragma: no cover
        raise RuntimeError("EmbeddingService has not been initialised.")
    return _embedding_service


async def get_qdrant_service() -> QdrantService:
    if _qdrant_service is None:  # pragma: no cover
        raise RuntimeError("QdrantService has not been initialised.")
    return _qdrant_service


# ---------------------------------------------------------------------------
# JWT / auth dependencies
# ---------------------------------------------------------------------------

class CurrentUser:
    def __init__(
        self,
        user_id: uuid.UUID,
        role: str,
        email: str,
    ) -> None:
        self.user_id = user_id
        self.role = role
        self.email = email

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_vendor(self) -> bool:
        return self.role in ("vendor", "admin")


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> CurrentUser:
    token = credentials.credentials

    # Check blacklist in Redis
    blacklisted = await redis.get(f"blacklist:{token}")
    if blacklisted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked.",
        )

    if not settings.jwt_public_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT public key not configured.",
        )

    try:
        payload = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except JWTError as exc:
        logger.debug("JWT decode failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        ) from exc

    user_id_str: Optional[str] = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject.",
        )

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID in token.",
        )

    role: str = payload.get("role", "customer")
    email: str = payload.get("email", "")

    return CurrentUser(user_id=user_id, role=role, email=email)


async def require_admin(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return current_user


async def require_vendor_or_admin(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    if not current_user.is_vendor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vendor or admin privileges required.",
        )
    return current_user
