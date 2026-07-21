from typing import Annotated, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, get_redis, require_admin
from app.services import inventory_service

router = APIRouter(prefix="/inventory", tags=["inventory"])


class SetInventoryRequest(BaseModel):
    product_id: str
    quantity: int
    low_stock_threshold: int = 10
    warehouse_id: str = "main"


class AdjustQuantityRequest(BaseModel):
    quantity_delta: int
    note: Optional[str] = None
    reference_id: Optional[str] = None


class ReserveRequest(BaseModel):
    quantity: int
    order_id: Optional[str] = None


class ReleaseRequest(BaseModel):
    quantity: int
    order_id: Optional[str] = None


@router.get("/low-stock")
async def get_low_stock(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    return await inventory_service.get_low_stock(db)


@router.get("/{product_id}")
async def get_inventory(
    product_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await inventory_service.get_inventory(db, product_id)


@router.post("/", status_code=status.HTTP_201_CREATED)
async def set_inventory(
    body: SetInventoryRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    return await inventory_service.set_inventory(
        db, redis, body.product_id, body.quantity, body.low_stock_threshold, body.warehouse_id
    )


@router.patch("/{product_id}")
async def adjust_quantity(
    product_id: str,
    body: AdjustQuantityRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    return await inventory_service.adjust_quantity(
        db, redis, product_id, body.quantity_delta, body.reference_id, body.note
    )


@router.post("/{product_id}/reserve", status_code=status.HTTP_200_OK)
async def reserve_stock(
    product_id: str,
    body: ReserveRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # No auth required — internal service-to-service call
    return await inventory_service.reserve_stock(db, product_id, body.quantity, body.order_id)


@router.post("/{product_id}/release", status_code=status.HTTP_200_OK)
async def release_reservation(
    product_id: str,
    body: ReleaseRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # No auth required — internal service-to-service call
    return await inventory_service.release_reservation(db, product_id, body.quantity, body.order_id)


@router.get("/{product_id}/movements")
async def get_movements(
    product_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return await inventory_service.get_movements(db, product_id)
