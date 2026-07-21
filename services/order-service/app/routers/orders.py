from typing import Annotated, Optional

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import get_current_user, get_http_client, get_redis, require_admin
from app.services import order_service

router = APIRouter(prefix="/orders", tags=["orders"])


class OrderItemIn(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    unit_price: float
    total_price: float


class CreateOrderRequest(BaseModel):
    user_id: Optional[str] = None
    items: list[OrderItemIn]
    shipping_address: dict
    subtotal: float
    discount: float = 0.0
    total: float
    coupon_code: Optional[str] = None
    shipping_fee: float = 0.0


class UpdateStatusRequest(BaseModel):
    status: str
    note: Optional[str] = None


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_order(
    body: CreateOrderRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    user_id = body.user_id or str(current_user.get("sub", current_user.get("user_id")))
    return await order_service.create_order(
        db, redis, user_id,
        [i.model_dump() for i in body.items],
        body.shipping_address, body.subtotal, body.discount, body.total,
        body.coupon_code, body.shipping_fee,
    )


@router.get("/")
async def list_orders(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    user_id = str(current_user.get("sub", current_user.get("user_id")))
    is_admin = current_user.get("role") in ("admin", "vendor")
    return await order_service.list_orders(db, user_id, is_admin, status_filter, page, page_size)


@router.get("/{order_id}")
async def get_order(
    order_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    user_id = str(current_user.get("sub", current_user.get("user_id")))
    is_admin = current_user.get("role") in ("admin", "vendor")
    return await order_service.get_order(db, order_id, user_id, is_admin)


@router.patch("/{order_id}/status")
async def update_status(
    order_id: str,
    body: UpdateStatusRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    changed_by = str(current_user.get("sub", current_user.get("user_id")))
    return await order_service.update_status(db, redis, order_id, body.status, body.note, changed_by)


@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    http_client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    current_user: Annotated[dict, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    user_id = str(current_user.get("sub", current_user.get("user_id")))
    return await order_service.cancel_order(db, redis, http_client, order_id, user_id, settings.INVENTORY_SERVICE_URL)


@router.get("/{order_id}/history")
async def get_order_history(
    order_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return await order_service.get_order_history(db, order_id)
