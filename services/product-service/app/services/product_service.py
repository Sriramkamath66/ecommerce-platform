from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.product import Category, Product, ProductImage
from app.schemas.product import (
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
    SearchResult,
)
from app.services.embedding_service import EmbeddingService
from app.services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_product_response(product: Product) -> ProductResponse:
    category_name: Optional[str] = None
    if product.category is not None:
        category_name = product.category.name
    return ProductResponse.from_orm_with_category(product, category_name=category_name)


def _product_payload(product: Product) -> dict:
    """Build the Qdrant payload stored alongside the vector."""
    return {
        "name": product.name,
        "sku": product.sku,
        "price": float(product.price),
        "category_id": str(product.category_id) if product.category_id else None,
        "vendor_id": str(product.vendor_id) if product.vendor_id else None,
        "is_active": product.is_active,
        "tags": product.tags,
    }


async def _publish_event(redis: Redis, event: str, payload: dict) -> None:
    try:
        await redis.publish(
            f"product.{event}",
            json.dumps(payload, default=str),
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to publish event product.%s: %s", event, exc)


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------

async def create_product(
    db: AsyncSession,
    data: ProductCreate,
    embedding_svc: EmbeddingService,
    qdrant_svc: QdrantService,
    redis: Redis,
    vendor_id: Optional[uuid.UUID] = None,
) -> ProductResponse:
    product = Product(
        name=data.name,
        description=data.description,
        price=data.price,
        compare_at_price=data.compare_at_price,
        sku=data.sku,
        category_id=data.category_id,
        vendor_id=vendor_id,
        tags=data.tags,
        metadata_=data.metadata_,
        is_active=True,
    )
    db.add(product)

    # Add images
    for img_data in data.images:
        image = ProductImage(
            product_id=product.id,
            url=img_data.url,
            alt_text=img_data.alt_text,
            is_primary=img_data.is_primary,
            sort_order=img_data.sort_order,
        )
        db.add(image)

    await db.flush()

    # Reload with relationships
    result = await db.execute(
        select(Product)
        .options(selectinload(Product.images), selectinload(Product.category))
        .where(Product.id == product.id)
    )
    product = result.scalar_one()

    # Generate embedding and push to Qdrant
    try:
        vector = await embedding_svc.embed_product(product)
        await qdrant_svc.upsert_product(product.id, vector, _product_payload(product))
    except Exception as exc:
        logger.error("Embedding/Qdrant upsert failed for product %s: %s", product.id, exc)

    # Publish event
    await _publish_event(redis, "created", {"product_id": str(product.id), "sku": product.sku})

    return _build_product_response(product)


async def get_product(db: AsyncSession, product_id: uuid.UUID) -> Optional[ProductResponse]:
    result = await db.execute(
        select(Product)
        .options(selectinload(Product.images), selectinload(Product.category))
        .where(Product.id == product_id)
    )
    product = result.scalar_one_or_none()
    if product is None:
        return None
    return _build_product_response(product)


async def list_products(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    category_id: Optional[uuid.UUID] = None,
    min_price: Optional[Decimal] = None,
    max_price: Optional[Decimal] = None,
    is_active: Optional[bool] = True,
) -> ProductListResponse:
    query = select(Product).options(
        selectinload(Product.images), selectinload(Product.category)
    )
    count_query = select(func.count()).select_from(Product)

    if category_id is not None:
        query = query.where(Product.category_id == category_id)
        count_query = count_query.where(Product.category_id == category_id)
    if min_price is not None:
        query = query.where(Product.price >= min_price)
        count_query = count_query.where(Product.price >= min_price)
    if max_price is not None:
        query = query.where(Product.price <= max_price)
        count_query = count_query.where(Product.price <= max_price)
    if is_active is not None:
        query = query.where(Product.is_active == is_active)
        count_query = count_query.where(Product.is_active == is_active)

    total_result = await db.execute(count_query)
    total: int = total_result.scalar_one()

    offset = (page - 1) * page_size
    query = query.order_by(Product.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    products = result.scalars().all()

    return ProductListResponse(
        items=[_build_product_response(p) for p in products],
        total=total,
        page=page,
        page_size=page_size,
    )


async def update_product(
    db: AsyncSession,
    product_id: uuid.UUID,
    data: ProductUpdate,
    embedding_svc: EmbeddingService,
    qdrant_svc: QdrantService,
    redis: Redis,
) -> Optional[ProductResponse]:
    result = await db.execute(
        select(Product)
        .options(selectinload(Product.images), selectinload(Product.category))
        .where(Product.id == product_id)
    )
    product = result.scalar_one_or_none()
    if product is None:
        return None

    needs_reembedding = False
    update_data = data.model_dump(exclude_none=True, by_alias=False)

    for field, value in update_data.items():
        if field == "images":
            continue
        if field in ("name", "description", "tags"):
            needs_reembedding = True
        setattr(product, field, value)

    # Handle image replacement if provided
    if data.images is not None:
        # Remove existing images
        for img in list(product.images):
            await db.delete(img)
        await db.flush()
        # Add new images
        for img_data in data.images:
            image = ProductImage(
                product_id=product.id,
                url=img_data.url,
                alt_text=img_data.alt_text,
                is_primary=img_data.is_primary,
                sort_order=img_data.sort_order,
            )
            db.add(image)

    await db.flush()

    # Reload
    result = await db.execute(
        select(Product)
        .options(selectinload(Product.images), selectinload(Product.category))
        .where(Product.id == product_id)
    )
    product = result.scalar_one()

    # Re-embed only when semantically relevant fields changed
    if needs_reembedding:
        try:
            vector = await embedding_svc.embed_product(product)
            await qdrant_svc.upsert_product(product.id, vector, _product_payload(product))
        except Exception as exc:
            logger.error("Re-embedding failed for product %s: %s", product.id, exc)
    else:
        # Still update the Qdrant payload (price, active state, etc. may have changed)
        try:
            vector = await embedding_svc.embed_product(product)
            await qdrant_svc.upsert_product(product.id, vector, _product_payload(product))
        except Exception as exc:
            logger.error("Qdrant payload update failed for product %s: %s", product.id, exc)

    await _publish_event(redis, "updated", {"product_id": str(product.id), "sku": product.sku})

    return _build_product_response(product)


async def delete_product(
    db: AsyncSession,
    product_id: uuid.UUID,
    qdrant_svc: QdrantService,
    redis: Redis,
) -> bool:
    result = await db.execute(
        select(Product).where(Product.id == product_id)
    )
    product = result.scalar_one_or_none()
    if product is None:
        return False

    # Soft delete
    product.is_active = False
    await db.flush()

    # Remove from Qdrant so it no longer appears in searches
    try:
        await qdrant_svc.delete_product(product_id)
    except Exception as exc:
        logger.error("Qdrant delete failed for product %s: %s", product_id, exc)

    await _publish_event(redis, "deleted", {"product_id": str(product_id)})
    return True


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------

async def search_products(
    db: AsyncSession,
    query: str,
    embedding_svc: EmbeddingService,
    qdrant_svc: QdrantService,
    limit: int = 10,
    category_id: Optional[uuid.UUID] = None,
    min_price: Optional[Decimal] = None,
    max_price: Optional[Decimal] = None,
) -> list[SearchResult]:
    query_vector = await embedding_svc.embed_text(query)

    filters: dict = {}
    if category_id is not None:
        filters["category_id"] = category_id
    if min_price is not None:
        filters["min_price"] = min_price
    if max_price is not None:
        filters["max_price"] = max_price

    hits = await qdrant_svc.search_products(query_vector, limit, filters or None)

    if not hits:
        return []

    # Fetch full product data from DB for matched IDs
    product_ids = [uuid.UUID(str(h["id"])) for h in hits]
    db_result = await db.execute(
        select(Product)
        .options(selectinload(Product.images), selectinload(Product.category))
        .where(Product.id.in_(product_ids))
    )
    products_by_id: dict[uuid.UUID, Product] = {
        p.id: p for p in db_result.scalars().all()
    }

    results: list[SearchResult] = []
    for hit in hits:
        pid = uuid.UUID(str(hit["id"]))
        product = products_by_id.get(pid)
        if product is None or not product.is_active:
            continue
        results.append(
            SearchResult(
                product=_build_product_response(product),
                score=hit["score"],
            )
        )

    return results
