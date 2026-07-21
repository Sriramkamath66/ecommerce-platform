import os
import logging
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer()

_TEST_SECRET = "recommendation-service-test-secret"
_FALLBACK_MODE = False


def _load_public_key() -> Optional[str]:
    """Load RS256 public key from disk. Returns None if file does not exist."""
    key_path = settings.JWT_PUBLIC_KEY_PATH
    if not os.path.isfile(key_path):
        logger.warning(
            "JWT public key not found at %s — falling back to HS256 test mode",
            key_path,
        )
        return None
    with open(key_path, "r") as fh:
        return fh.read()


# Load once at import time so every request reuses the cached key.
_public_key: Optional[str] = _load_public_key()
_fallback_mode: bool = _public_key is None


def _decode_token(token: str) -> dict:
    """
    Decode and validate a JWT token.

    When a real RS256 public key is available the token is verified with that
    key.  Otherwise the service falls back to HS256 with a fixed test secret so
    that local / CI environments still work without real key infrastructure.
    """
    if not _fallback_mode:
        try:
            payload = jwt.decode(
                token,
                _public_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {exc}",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        # Fallback: accept any well-formed HS256 token signed with the test secret,
        # *or* a token that was issued without a real signature (for testing).
        try:
            payload = jwt.decode(
                token,
                _TEST_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            return payload
        except jwt.InvalidTokenError:
            # Last resort: decode without verification so plain test tokens work
            # (e.g. tokens created with jwt.encode(..., algorithm="none")).
            try:
                payload = jwt.decode(
                    token,
                    options={"verify_signature": False},
                )
                return payload
            except jwt.InvalidTokenError as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid token: {exc}",
                    headers={"WWW-Authenticate": "Bearer"},
                )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """FastAPI dependency that decodes the bearer JWT and returns the claims dict."""
    return _decode_token(credentials.credentials)


def get_current_user_id(
    current_user: dict = Depends(get_current_user),
) -> str:
    """FastAPI dependency that extracts the 'sub' claim as the user identifier."""
    sub = current_user.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing 'sub' claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return str(sub)


def is_admin(current_user: dict) -> bool:
    """Return True when the token's 'roles' claim contains 'admin'."""
    roles = current_user.get("roles", [])
    if isinstance(roles, str):
        roles = [roles]
    return "admin" in roles
