import json
import secrets
import uuid
from decimal import Decimal
from typing import Optional

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.payment import Payment, PaymentMethod, PaymentStatus, Refund


def _payment_to_dict(payment: Payment) -> dict:
    return {
        "id": str(payment.id),
        "order_id": str(payment.order_id),
        "user_id": str(payment.user_id),
        "amount": float(payment.amount),
        "currency": payment.currency,
        "status": payment.status,
        "method": payment.method,
        "provider_ref": payment.provider_ref,
        "failure_reason": payment.failure_reason,
        "metadata": payment.metadata_,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
        "updated_at": payment.updated_at.isoformat() if payment.updated_at else None,
        "refunds": [
            {
                "id": str(r.id),
                "payment_id": str(r.payment_id),
                "amount": float(r.amount),
                "reason": r.reason,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in (payment.refunds or [])
        ],
    }


async def initiate_payment(
    db: AsyncSession,
    redis: aioredis.Redis,
    order_id: str,
    user_id: str,
    amount: float,
    method: str,
    currency: str = "USD",
    metadata: Optional[dict] = None,
) -> dict:
    provider_ref = "pi_" + secrets.token_hex(16)
    payment = Payment(
        id=uuid.uuid4(),
        order_id=uuid.UUID(order_id),
        user_id=uuid.UUID(user_id),
        amount=Decimal(str(amount)),
        currency=currency,
        status=PaymentStatus.PENDING,
        method=method,
        provider_ref=provider_ref,
        metadata_=metadata or {},
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    # Load relationships
    result = await db.execute(
        select(Payment).options(selectinload(Payment.refunds)).where(Payment.id == payment.id)
    )
    payment = result.scalar_one()
    return _payment_to_dict(payment)


async def confirm_payment(db: AsyncSession, redis: aioredis.Redis, payment_id: str) -> dict:
    result = await db.execute(
        select(Payment).options(selectinload(Payment.refunds)).where(Payment.id == uuid.UUID(payment_id))
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.status not in (PaymentStatus.PENDING, PaymentStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot confirm payment with status {payment.status}",
        )

    payment.status = PaymentStatus.COMPLETED
    await db.commit()
    await db.refresh(payment)

    # Publish event
    event = json.dumps({
        "order_id": str(payment.order_id),
        "payment_id": str(payment.id),
        "amount": float(payment.amount),
        "currency": payment.currency,
    })
    await redis.publish("payment.completed", event)

    return _payment_to_dict(payment)


async def fail_payment(db: AsyncSession, redis: aioredis.Redis, payment_id: str, reason: str) -> dict:
    result = await db.execute(
        select(Payment).options(selectinload(Payment.refunds)).where(Payment.id == uuid.UUID(payment_id))
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.status in (PaymentStatus.COMPLETED, PaymentStatus.REFUNDED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot fail payment with status {payment.status}",
        )

    payment.status = PaymentStatus.FAILED
    payment.failure_reason = reason
    await db.commit()
    await db.refresh(payment)

    event = json.dumps({
        "order_id": str(payment.order_id),
        "payment_id": str(payment.id),
        "reason": reason,
    })
    await redis.publish("payment.failed", event)

    return _payment_to_dict(payment)


async def process_webhook(db: AsyncSession, redis: aioredis.Redis, payload: dict) -> dict:
    """
    Process Stripe-style webhook payload.
    Expected payload: {"type": "payment_intent.succeeded"|"payment_intent.payment_failed", "data": {"object": {"id": "pi_xxx", "metadata": {...}}}}
    """
    event_type = payload.get("type", "")
    data_obj = payload.get("data", {}).get("object", {})
    provider_ref = data_obj.get("id", "")

    if not provider_ref:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook payload: missing provider ref")

    result = await db.execute(
        select(Payment).options(selectinload(Payment.refunds)).where(Payment.provider_ref == provider_ref)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        # Unknown payment — silently acknowledge
        return {"received": True, "action": "skipped"}

    if event_type in ("payment_intent.succeeded", "charge.succeeded"):
        return await confirm_payment(db, redis, str(payment.id))
    elif event_type in ("payment_intent.payment_failed", "charge.failed"):
        reason = data_obj.get("last_payment_error", {}).get("message", "Payment failed")
        return await fail_payment(db, redis, str(payment.id), reason)
    else:
        return {"received": True, "action": "unhandled", "type": event_type}


async def create_refund(
    db: AsyncSession,
    redis: aioredis.Redis,
    payment_id: str,
    amount: float,
    reason: str,
) -> dict:
    result = await db.execute(
        select(Payment).options(selectinload(Payment.refunds)).where(Payment.id == uuid.UUID(payment_id))
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.status != PaymentStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can only refund completed payments. Current status: {payment.status}",
        )

    refund_amount = Decimal(str(amount))
    if refund_amount > payment.amount:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Refund amount exceeds payment amount")

    refund = Refund(
        id=uuid.uuid4(),
        payment_id=payment.id,
        amount=refund_amount,
        reason=reason,
        status="completed",
    )
    db.add(refund)
    payment.status = PaymentStatus.REFUNDED
    await db.commit()
    await db.refresh(payment)

    event = json.dumps({
        "order_id": str(payment.order_id),
        "payment_id": str(payment.id),
        "refund_amount": float(refund_amount),
        "reason": reason,
    })
    await redis.publish("payment.refunded", event)

    return _payment_to_dict(payment)


async def get_payment(db: AsyncSession, payment_id: str) -> dict:
    result = await db.execute(
        select(Payment).options(selectinload(Payment.refunds)).where(Payment.id == uuid.UUID(payment_id))
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return _payment_to_dict(payment)


async def list_payments(db: AsyncSession, user_id: str, is_admin: bool = False) -> list[dict]:
    query = select(Payment).options(selectinload(Payment.refunds))
    if not is_admin:
        query = query.where(Payment.user_id == uuid.UUID(user_id))
    query = query.order_by(Payment.created_at.desc())
    result = await db.execute(query)
    payments = result.scalars().all()
    return [_payment_to_dict(p) for p in payments]
