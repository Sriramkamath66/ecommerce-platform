from __future__ import annotations

from fastapi import APIRouter, Depends, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_redis
from app.schemas.user import (
    ForgotPasswordRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await auth_service.register_user(db, data)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate with email + password, receive JWT pair",
)
async def login(
    data: UserLogin,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TokenResponse:
    user = await auth_service.authenticate_user(db, data.email, data.password)
    return await auth_service.create_tokens(user, redis)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token and receive a new JWT pair",
)
async def refresh(
    data: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TokenResponse:
    return await auth_service.refresh_tokens(db, redis, data.refresh_token)


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Revoke the supplied refresh token",
)
async def logout(
    data: RefreshRequest,
    redis: Redis = Depends(get_redis),
) -> dict:
    await auth_service.logout(redis, data.refresh_token)
    return {"message": "Successfully logged out."}


@router.post(
    "/forgot-password",
    status_code=status.HTTP_200_OK,
    summary="Request a password-reset email (always returns 200 to prevent enumeration)",
)
async def forgot_password(
    data: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    await auth_service.initiate_password_reset(db, redis, data.email)
    return {
        "message": "If that email address is registered, a reset link has been sent."
    }


@router.post(
    "/reset-password",
    status_code=status.HTTP_200_OK,
    summary="Consume a password-reset token and set a new password",
)
async def reset_password(
    data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    await auth_service.reset_password(db, redis, data.token, data.new_password)
    return {"message": "Password has been reset successfully."}


@router.get(
    "/public-key",
    summary="Return the RS256 public key (used by other services to verify JWTs)",
)
async def get_public_key() -> dict:
    return {"public_key": settings.jwt_public_key}
