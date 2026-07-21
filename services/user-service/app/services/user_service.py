from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import UserNotFoundError
from app.models.user import User, UserProfile
from app.schemas.user import (
    PaginatedUserResponse,
    UserProfileResponse,
    UserProfileUpdate,
    UserResponse,
)

logger = logging.getLogger(__name__)


async def get_user_by_id(db: AsyncSession, user_id: UUID) -> UserResponse:
    """Fetch a single user by primary key and return the response schema."""
    result = await db.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        raise UserNotFoundError(f"User '{user_id}' not found.")

    return UserResponse.model_validate(user)


async def get_profile(db: AsyncSession, user_id: UUID) -> UserProfileResponse:
    """Fetch the :class:`UserProfile` for *user_id*."""
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile: UserProfile | None = result.scalar_one_or_none()

    if profile is None:
        raise UserNotFoundError(f"Profile for user '{user_id}' not found.")

    return UserProfileResponse.model_validate(profile)


async def update_profile(
    db: AsyncSession, user_id: UUID, data: UserProfileUpdate
) -> UserProfileResponse:
    """Apply partial updates to the :class:`UserProfile` and return the updated record."""
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile: UserProfile | None = result.scalar_one_or_none()

    if profile is None:
        raise UserNotFoundError(f"Profile for user '{user_id}' not found.")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)

    logger.debug("Updated profile for user id=%s fields=%s", user_id, list(update_data))
    return UserProfileResponse.model_validate(profile)


async def list_users(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
) -> PaginatedUserResponse:
    """Return a paginated list of all users."""
    # Total count
    count_result = await db.execute(select(func.count()).select_from(User))
    total: int = count_result.scalar_one()

    # Page of users
    users_result = await db.execute(
        select(User)
        .order_by(User.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    users = list(users_result.scalars().all())

    return PaginatedUserResponse(
        items=[UserResponse.model_validate(u) for u in users],
        total=total,
        skip=skip,
        limit=limit,
    )
