from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import (
    InvalidCredentialsError,
    InvalidTokenError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from app.models.user import User, UserProfile
from app.schemas.user import TokenResponse, UserCreate, UserResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password*."""
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches the *hashed* bcrypt digest."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user: User) -> str:
    """Mint a short-lived RS256 access JWT."""
    now = _now_utc()
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": str(user.role),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")


def create_refresh_token(user: User) -> str:
    """Mint a long-lived RS256 refresh JWT that includes a unique jti."""
    now = _now_utc()
    payload = {
        "sub": str(user.id),
        "type": "refresh",
        "jti": secrets.token_hex(16),
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")


def decode_token(token: str) -> dict:
    """Decode and verify an RS256 JWT, raising :exc:`InvalidTokenError` on failure."""
    try:
        return jwt.decode(token, settings.jwt_public_key, algorithms=["RS256"])
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("Token is invalid.") from exc


def _refresh_redis_key(refresh_token: str) -> str:
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    return f"refresh:{token_hash}"


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------


async def register_user(db: AsyncSession, data: UserCreate) -> UserResponse:
    """Create a new user account and an empty profile."""
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none() is not None:
        raise UserAlreadyExistsError(
            f"A user with email '{data.email}' already exists."
        )

    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
    )
    db.add(user)
    await db.flush()  # get the generated id before creating the profile

    profile = UserProfile(user_id=user.id, addresses=[])
    db.add(profile)
    await db.commit()
    await db.refresh(user)

    logger.info("Registered new user id=%s email=%s", user.id, user.email)
    return UserResponse.model_validate(user)


async def authenticate_user(
    db: AsyncSession, email: str, password: str
) -> User:
    """Verify credentials and return the :class:`User` model instance."""
    result = await db.execute(select(User).where(User.email == email))
    user: User | None = result.scalar_one_or_none()

    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError()

    if not user.is_active:
        raise InvalidCredentialsError("This account is deactivated.")

    return user


async def create_tokens(user: User, redis: Redis) -> TokenResponse:
    """Issue an access + refresh token pair, persisting the refresh token in Redis."""
    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)

    ttl_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
    await redis.setex(
        _refresh_redis_key(refresh_token),
        ttl_seconds,
        str(user.id),
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


async def refresh_tokens(
    db: AsyncSession, redis: Redis, refresh_token: str
) -> TokenResponse:
    """Rotate refresh tokens: verify the old one, delete it, issue a new pair."""
    payload = decode_token(refresh_token)

    if payload.get("type") != "refresh":
        raise InvalidTokenError("Supplied token is not a refresh token.")

    redis_key = _refresh_redis_key(refresh_token)
    stored_user_id: str | None = await redis.get(redis_key)

    if stored_user_id is None:
        raise InvalidTokenError("Refresh token has been revoked or does not exist.")

    # Invalidate the consumed refresh token immediately (rotation)
    await redis.delete(redis_key)

    user_id = UUID(stored_user_id)
    result = await db.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        raise InvalidTokenError("User associated with token not found.")

    if not user.is_active:
        raise InvalidTokenError("This account is deactivated.")

    return await create_tokens(user, redis)


async def logout(redis: Redis, refresh_token: str) -> None:
    """Revoke a refresh token by removing it from Redis."""
    await redis.delete(_refresh_redis_key(refresh_token))


async def initiate_password_reset(
    db: AsyncSession, redis: Redis, email: str
) -> None:
    """Generate a password-reset token and store it in Redis.

    Always returns successfully to prevent email-enumeration attacks.
    In production the token would be emailed to the user.
    """
    result = await db.execute(select(User).where(User.email == email))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        # Return silently — do not reveal whether the email exists
        logger.debug("Password reset requested for unknown email: %s", email)
        return

    token = secrets.token_urlsafe(32)
    ttl_seconds = 3600  # 1 hour
    await redis.setex(f"pwd_reset:{token}", ttl_seconds, str(user.id))

    # TODO: send email via aiosmtplib/Jinja2 template
    logger.info(
        "Password reset token created for user id=%s (token omitted from log)",
        user.id,
    )


async def reset_password(
    db: AsyncSession, redis: Redis, token: str, new_password: str
) -> None:
    """Consume a password-reset token and update the user's password."""
    redis_key = f"pwd_reset:{token}"
    stored_user_id: str | None = await redis.get(redis_key)

    if stored_user_id is None:
        raise InvalidTokenError("Password reset token is invalid or has expired.")

    result = await db.execute(
        select(User).where(User.id == UUID(stored_user_id))
    )
    user: User | None = result.scalar_one_or_none()

    if user is None:
        raise UserNotFoundError()

    user.hashed_password = hash_password(new_password)
    await db.commit()
    await redis.delete(redis_key)

    logger.info("Password reset successfully for user id=%s", user.id)
