from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, require_admin
from app.schemas.product import CategoryCreate, CategoryResponse, CategoryUpdate
from app.services import category_service

router = APIRouter(prefix="/categories", tags=["categories"])


# ---------------------------------------------------------------------------
# GET /categories — list all
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[CategoryResponse])
async def list_categories(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[CategoryResponse]:
    return await category_service.list_categories(db=db)


# ---------------------------------------------------------------------------
# POST /categories — admin only
# ---------------------------------------------------------------------------

@router.post(
    "/", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED
)
async def create_category(
    data: CategoryCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[CurrentUser, Depends(require_admin)],
) -> CategoryResponse:
    return await category_service.create_category(db=db, data=data)


# ---------------------------------------------------------------------------
# GET /categories/{id}
# ---------------------------------------------------------------------------

@router.get("/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CategoryResponse:
    category = await category_service.get_category(db=db, category_id=category_id)
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category {category_id} not found.",
        )
    return category


# ---------------------------------------------------------------------------
# PATCH /categories/{id} — admin only
# ---------------------------------------------------------------------------

@router.patch("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: uuid.UUID,
    data: CategoryUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[CurrentUser, Depends(require_admin)],
) -> CategoryResponse:
    updated = await category_service.update_category(
        db=db, category_id=category_id, data=data
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category {category_id} not found.",
        )
    return updated


# ---------------------------------------------------------------------------
# DELETE /categories/{id} — admin only
# ---------------------------------------------------------------------------

@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[CurrentUser, Depends(require_admin)],
) -> Response:
    deleted = await category_service.delete_category(
        db=db, category_id=category_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category {category_id} not found.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
