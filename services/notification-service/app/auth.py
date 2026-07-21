import logging
import os

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

logger = logging.getLogger(__name__)
security = HTTPBearer()


def _load_jwt_key_and_algorithm() -> tuple[str, str]:
    """Load the RS256 public key from disk; fall back to HS256 test secret."""
    key_path = settings.JWT_PUBLIC_KEY_PATH
    if os.path.exists(key_path):
        with open(key_path, "r") as fh:
            public_key = fh.read()
        return public_key, "RS256"

    logger.warning(
        "JWT public key not found at %s — falling back to HS256 with 'test-secret'. "
        "Do NOT use this in production.",
        key_path,
    )
    return "test-secret", "HS256"


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Decode and validate the Bearer JWT, returning the payload dict."""
    token = credentials.credentials
    public_key, algorithm = _load_jwt_key_and_algorithm()
    try:
        payload: dict = jwt.decode(token, public_key, algorithms=[algorithm])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user_id(
    user: dict = Depends(get_current_user),
) -> str:
    """Extract the subject claim (user_id) from the validated JWT payload."""
    user_id: str | None = user.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing the 'sub' claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


def is_admin(user: dict = Depends(get_current_user)) -> bool:
    """Return True when the JWT payload carries role == 'admin'."""
    return user.get("role") == "admin"
