from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_embedding_service,
    get_qdrant_service,
    get_redis,
    require_vendor_or_admin,
)
from app.schemas.product import (
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
    SearchResult,
)
from app.services import product_service
from app.services.embedding_service import EmbeddingService
from app.services.qdrant_service import QdrantService

router = APIRouter(prefix="/products", tags=["products"])


# ---------------------------------------------------------------------------
# GET /products — paginated list with optional filters
# ---------------------------------------------------------------------------

@router.get("/", response_model=ProductListResponse)
async def list_products(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category_id: Optional[uuid.UUID] = Query(None),
    min_price: Optional[Decimal] = Query(None, ge=0),
    max_price: Optional[Decimal] = Query(None, ge=0),
    is_active: Optional[bool] = Query(True),
) -> ProductListResponse:
    return await product_service.list_products(
        db=db,
        page=page,
        page_size=page_size,
        category_id=category_id,
        min_price=min_price,
        max_price=max_price,
        is_active=is_active,
    )


# ---------------------------------------------------------------------------
# GET /products/search — semantic search (must come before /{product_id})
# ---------------------------------------------------------------------------

@router.get("/search", response_model=list[SearchResult])
async def search_products(
    db: Annotated[AsyncSession, Depends(get_db)],
    embedding_svc: Annotated[EmbeddingService, Depends(get_embedding_service)],
    qdrant_svc: Annotated[QdrantService, Depends(get_qdrant_service)],
    q: str = Query(..., min_length=1, description="Semantic search query"),
    limit: int = Query(10, ge=1, le=100),
    category_id: Optional[uuid.UUID] = Query(None),
    min_price: Optional[Decimal] = Query(None, ge=0),
    max_price: Optional[Decimal] = Query(None, ge=0),
) -> list[SearchResult]:
    return await product_service.search_products(
        db=db,
        query=q,
        embedding_svc=embedding_svc,
        qdrant_svc=qdrant_svc,
        limit=limit,
        category_id=category_id,
        min_price=min_price,
        max_price=max_price,
    )


# ---------------------------------------------------------------------------
# POST /products — create (vendor / admin only)
# ---------------------------------------------------------------------------

@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    embedding_svc: Annotated[EmbeddingService, Depends(get_embedding_service)],
    qdrant_svc: Annotated[QdrantService, Depends(get_qdrant_service)],
    redis: Annotated[Redis, Depends(get_redis)],
    current_user: Annotated[CurrentUser, Depends(require_vendor_or_admin)],
) -> ProductResponse:
    return await product_service.create_product(
        db=db,
        data=data,
        embedding_svc=embedding_svc,
        qdrant_svc=qdrant_svc,
        redis=redis,
        vendor_id=current_user.user_id,
    )


# ---------------------------------------------------------------------------
# GET /products/{product_id}
# ---------------------------------------------------------------------------

@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProductResponse:
    product = await product_service.get_product(db=db, product_id=product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found.",
        )
    return product


# ---------------------------------------------------------------------------
# PATCH /products/{product_id} — update (vendor must own product)
# ---------------------------------------------------------------------------

@router.patch("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: uuid.UUID,
    data: ProductUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    embedding_svc: Annotated[EmbeddingService, Depends(get_embedding_service)],
    qdrant_svc: Annotated[QdrantService, Depends(get_qdrant_service)],
    redis: Annotated[Redis, Depends(get_redis)],
    current_user: Annotated[CurrentUser, Depends(require_vendor_or_admin)],
) -> ProductResponse:
    # Vendors may only update their own products; admins can update any
    if not current_user.is_admin:
        existing = await product_service.get_product(db=db, product_id=product_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {product_id} not found.",
            )
        if existing.vendor_id != current_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not own this product.",
            )

    updated = await product_service.update_product(
        db=db,
        product_id=product_id,
        data=data,
        embedding_svc=embedding_svc,
        qdrant_svc=qdrant_svc,
        redis=redis,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found.",
        )
    return updated


# ---------------------------------------------------------------------------
# DELETE /products/{product_id} — soft delete (vendor must own product)
# ---------------------------------------------------------------------------

@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    qdrant_svc: Annotated[QdrantService, Depends(get_qdrant_service)],
    redis: Annotated[Redis, Depends(get_redis)],
    current_user: Annotated[CurrentUser, Depends(require_vendor_or_admin)],
) -> None:
    if not current_user.is_admin:
        existing = await product_service.get_product(db=db, product_id=product_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {product_id} not found.",
            )
        if existing.vendor_id != current_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not own this product.",
            )

    deleted = await product_service.delete_product(
        db=db,
        product_id=product_id,
        qdrant_svc=qdrant_svc,
        redis=redis,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found.",
        )
