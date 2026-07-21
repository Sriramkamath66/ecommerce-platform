import os
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .config import settings

security = HTTPBearer()

_public_key: str | None = None
_use_test_mode: bool = False


def _load_public_key() -> None:
    """Load the RS256 public key from disk, or fall back to HS256 test mode."""
    global _public_key, _use_test_mode
    key_path = settings.JWT_PUBLIC_KEY_PATH
    if os.path.exists(key_path):
        with open(key_path, "r") as f:
            _public_key = f.read()
        _use_test_mode = False
    else:
        # Test / local development: accept HS256 tokens signed with "test-secret"
        _public_key = "test-secret"
        _use_test_mode = True


def _decode_token(token: str) -> dict:
    """Decode and validate a JWT token, returning its claims payload."""
    global _public_key, _use_test_mode
    if _public_key is None:
        _load_public_key()

    try:
        if _use_test_mode:
            payload = jwt.decode(
                token,
                _public_key,
                algorithms=["HS256"],
                options={"verify_exp": True},
            )
        else:
            payload = jwt.decode(
                token,
                _public_key,
                algorithms=["RS256"],
                options={"verify_exp": True},
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


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Validate bearer token and return the decoded JWT claims."""
    return _decode_token(credentials.credentials)


def get_current_user_id(
    current_user: dict = Depends(get_current_user),
) -> str:
    """Extract the subject (user ID) from the validated JWT claims."""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing 'sub' claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return str(user_id)


def is_admin(current_user: dict) -> bool:
    """Return True if the token carries an admin role claim."""
    roles = current_user.get("roles", [])
    if isinstance(roles, list):
        return "admin" in roles
    return str(roles) == "admin"
