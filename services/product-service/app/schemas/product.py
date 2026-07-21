from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Image schemas
# ---------------------------------------------------------------------------

class ProductImageCreate(BaseModel):
    url: str = Field(..., max_length=2048)
    alt_text: Optional[str] = Field(None, max_length=512)
    is_primary: bool = False
    sort_order: int = 0


class ProductImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    url: str
    alt_text: Optional[str]
    is_primary: bool
    sort_order: int


# ---------------------------------------------------------------------------
# Category schemas
# ---------------------------------------------------------------------------

class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-z0-9-]+$")
    parent_id: Optional[uuid.UUID] = None
    description: Optional[str] = None

    @field_validator("slug")
    @classmethod
    def slug_lowercase(cls, v: str) -> str:
        return v.lower()


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    slug: Optional[str] = Field(None, min_length=1, max_length=255, pattern=r"^[a-z0-9-]+$")
    parent_id: Optional[uuid.UUID] = None
    description: Optional[str] = None


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: Optional[str]
    parent_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Product schemas
# ---------------------------------------------------------------------------

class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=512)
    description: str = Field(default="")
    price: Decimal = Field(..., gt=0, decimal_places=2)
    compare_at_price: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    sku: str = Field(..., min_length=1, max_length=255)
    category_id: Optional[uuid.UUID] = None
    tags: list[str] = Field(default_factory=list)
    metadata_: dict[str, Any] = Field(default_factory=dict, alias="metadata")
    images: list[ProductImageCreate] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=512)
    description: Optional[str] = None
    price: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    compare_at_price: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    sku: Optional[str] = Field(None, min_length=1, max_length=255)
    category_id: Optional[uuid.UUID] = None
    tags: Optional[list[str]] = None
    metadata_: Optional[dict[str, Any]] = Field(None, alias="metadata")
    images: Optional[list[ProductImageCreate]] = None
    is_active: Optional[bool] = None

    model_config = ConfigDict(populate_by_name=True)


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    name: str
    description: str
    price: Decimal
    compare_at_price: Optional[Decimal]
    sku: str
    category_id: Optional[uuid.UUID]
    category_name: Optional[str] = None
    vendor_id: Optional[uuid.UUID]
    is_active: bool
    tags: list[str]
    metadata_: Optional[dict[str, Any]] = Field(None, serialization_alias="metadata")
    images: list[ProductImageResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_with_category(
        cls,
        product: Any,
        category_name: Optional[str] = None,
    ) -> "ProductResponse":
        obj = cls.model_validate(product)
        obj.category_name = category_name
        return obj


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Search schemas
# ---------------------------------------------------------------------------

class SearchQuery(BaseModel):
    q: str = Field(..., min_length=1, description="Semantic search query")
    limit: int = Field(10, ge=1, le=100)
    category_id: Optional[uuid.UUID] = None
    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, ge=0)


class SearchResult(BaseModel):
    product: ProductResponse
    score: float
