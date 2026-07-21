from typing import Annotated, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, get_redis, require_admin
from app.services import payment_service

router = APIRouter(prefix="/payments", tags=["payments"])


class InitiatePaymentRequest(BaseModel):
    order_id: str
    amount: float
    method: str
    currency: str = "USD"
    metadata: Optional[dict] = None


class RefundRequest(BaseModel):
    amount: float
    reason: str


@router.post("/", status_code=status.HTTP_201_CREATED)
async def initiate_payment(
    body: InitiatePaymentRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    user_id = str(current_user.get("sub", current_user.get("user_id")))
    return await payment_service.initiate_payment(
        db, redis, body.order_id, user_id, body.amount, body.method, body.currency, body.metadata
    )


@router.get("/")
async def list_payments(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    user_id = str(current_user.get("sub", current_user.get("user_id")))
    is_admin = current_user.get("role") in ("admin", "vendor")
    return await payment_service.list_payments(db, user_id, is_admin)


@router.get("/{payment_id}")
async def get_payment(
    payment_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return await payment_service.get_payment(db, payment_id)


@router.post("/{payment_id}/confirm")
async def confirm_payment(
    payment_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Simulate payment confirmation — for dev/test only."""
    return await payment_service.confirm_payment(db, redis, payment_id)


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    """Stripe-style webhook endpoint — no auth required."""
    payload = await request.json()
    return await payment_service.process_webhook(db, redis, payload)


@router.post("/{payment_id}/refund")
async def refund_payment(
    payment_id: str,
    body: RefundRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return await payment_service.create_refund(db, redis, payment_id, body.amount, body.reason)
