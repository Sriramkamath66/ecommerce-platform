from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models.user import User
from app.schemas.user import (
    PaginatedUserResponse,
    UserProfileResponse,
    UserProfileUpdate,
    UserResponse,
)
from app.services import user_service

router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Return the profile of the authenticated user",
)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.patch(
    "/me/profile",
    response_model=UserProfileResponse,
    summary="Partially update the authenticated user's profile",
)
async def update_my_profile(
    data: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    return await user_service.update_profile(db, current_user.id, data)


@router.get(
    "/me/profile",
    response_model=UserProfileResponse,
    summary="Return the authenticated user's profile details",
)
async def get_my_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    return await user_service.get_profile(db, current_user.id)


# ---------------------------------------------------------------------------
# Admin-only endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=PaginatedUserResponse,
    summary="[Admin] List all users with pagination",
    dependencies=[Depends(require_admin)],
)
async def list_users(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=100, description="Maximum records to return"),
    db: AsyncSession = Depends(get_db),
) -> PaginatedUserResponse:
    return await user_service.list_users(db, skip=skip, limit=limit)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="[Admin] Fetch any user by ID",
    dependencies=[Depends(require_admin)],
)
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await user_service.get_user_by_id(db, user_id)
