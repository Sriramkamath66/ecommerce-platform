from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Category
from app.schemas.product import CategoryCreate, CategoryResponse, CategoryUpdate

logger = logging.getLogger(__name__)


async def create_category(db: AsyncSession, data: CategoryCreate) -> CategoryResponse:
    category = Category(
        name=data.name,
        slug=data.slug,
        description=data.description,
        parent_id=data.parent_id,
    )
    db.add(category)
    await db.flush()
    await db.refresh(category)
    return CategoryResponse.model_validate(category)


async def get_category(
    db: AsyncSession, category_id: uuid.UUID
) -> Optional[CategoryResponse]:
    result = await db.execute(
        select(Category).where(Category.id == category_id)
    )
    category = result.scalar_one_or_none()
    if category is None:
        return None
    return CategoryResponse.model_validate(category)


async def list_categories(db: AsyncSession) -> list[CategoryResponse]:
    result = await db.execute(select(Category).order_by(Category.name))
    categories = result.scalars().all()
    return [CategoryResponse.model_validate(c) for c in categories]


async def update_category(
    db: AsyncSession,
    category_id: uuid.UUID,
    data: CategoryUpdate,
) -> Optional[CategoryResponse]:
    result = await db.execute(
        select(Category).where(Category.id == category_id)
    )
    category = result.scalar_one_or_none()
    if category is None:
        return None

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(category, field, value)

    await db.flush()
    await db.refresh(category)
    return CategoryResponse.model_validate(category)


async def delete_category(db: AsyncSession, category_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(Category).where(Category.id == category_id)
    )
    category = result.scalar_one_or_none()
    if category is None:
        return False
    await db.delete(category)
    await db.flush()
    return True
