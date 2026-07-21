import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

import httpx
import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.order import Order, OrderItem, OrderStatus, OrderStatusHistory

# Valid FSM transitions
VALID_TRANSITIONS: dict[str, list[str]] = {
    OrderStatus.PENDING: [OrderStatus.CONFIRMED, OrderStatus.CANCELLED],
    OrderStatus.CONFIRMED: [OrderStatus.PROCESSING, OrderStatus.CANCELLED],
    OrderStatus.PROCESSING: [OrderStatus.SHIPPED, OrderStatus.CANCELLED],
    OrderStatus.SHIPPED: [OrderStatus.DELIVERED],
    OrderStatus.DELIVERED: [OrderStatus.REFUNDED],
    OrderStatus.CANCELLED: [],
    OrderStatus.REFUNDED: [],
}


def _order_to_dict(order: Order) -> dict:
    return {
        "id": str(order.id),
        "user_id": str(order.user_id),
        "status": order.status,
        "subtotal": float(order.subtotal),
        "discount": float(order.discount),
        "shipping_fee": float(order.shipping_fee),
        "total": float(order.total),
        "shipping_address": order.shipping_address,
        "coupon_code": order.coupon_code,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
        "items": [
            {
                "id": str(item.id),
                "product_id": str(item.product_id),
                "product_name": item.product_name,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price),
                "total_price": float(item.total_price),
            }
            for item in (order.items or [])
        ],
    }


async def create_order(
    db: AsyncSession,
    redis: aioredis.Redis,
    user_id: str,
    items: list[dict],
    shipping_address: dict,
    subtotal: float,
    discount: float,
    total: float,
    coupon_code: Optional[str] = None,
    shipping_fee: float = 0.0,
) -> dict:
    order = Order(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
        status=OrderStatus.PENDING,
        subtotal=Decimal(str(subtotal)),
        discount=Decimal(str(discount)),
        shipping_fee=Decimal(str(shipping_fee)),
        total=Decimal(str(total)),
        shipping_address=shipping_address,
        coupon_code=coupon_code,
    )
    db.add(order)
    await db.flush()

    for item in items:
        order_item = OrderItem(
            id=uuid.uuid4(),
            order_id=order.id,
            product_id=uuid.UUID(str(item["product_id"])),
            product_name=item.get("product_name", "Unknown"),
            quantity=item["quantity"],
            unit_price=Decimal(str(item["unit_price"])),
            total_price=Decimal(str(item["total_price"])),
        )
        db.add(order_item)

    history = OrderStatusHistory(
        id=uuid.uuid4(),
        order_id=order.id,
        status=OrderStatus.PENDING,
        note="Order created",
    )
    db.add(history)
    await db.commit()
    await db.refresh(order)

    # Load relationships
    result = await db.execute(
        select(Order).options(selectinload(Order.items), selectinload(Order.history)).where(Order.id == order.id)
    )
    order = result.scalar_one()

    # Publish event
    event = json.dumps({"order_id": str(order.id), "user_id": user_id, "total": total})
    await redis.publish("order.created", event)

    return _order_to_dict(order)


async def get_order(db: AsyncSession, order_id: str, user_id: str, is_admin: bool = False) -> dict:
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.history))
        .where(Order.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if not is_admin and str(order.user_id) != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return _order_to_dict(order)


async def list_orders(
    db: AsyncSession,
    user_id: str,
    is_admin: bool = False,
    status_filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> list[dict]:
    query = select(Order).options(selectinload(Order.items))
    if not is_admin:
        query = query.where(Order.user_id == uuid.UUID(user_id))
    if status_filter:
        query = query.where(Order.status == status_filter)
    query = query.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    orders = result.scalars().all()
    return [_order_to_dict(o) for o in orders]


async def update_status(
    db: AsyncSession,
    redis: aioredis.Redis,
    order_id: str,
    new_status: str,
    note: Optional[str] = None,
    changed_by: Optional[str] = None,
) -> dict:
    result = await db.execute(
        select(Order).options(selectinload(Order.items), selectinload(Order.history)).where(Order.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    allowed = VALID_TRANSITIONS.get(order.status, [])
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition from {order.status} to {new_status}",
        )

    order.status = new_status
    history = OrderStatusHistory(
        id=uuid.uuid4(),
        order_id=order.id,
        status=new_status,
        note=note,
        changed_by=uuid.UUID(changed_by) if changed_by else None,
    )
    db.add(history)
    await db.commit()
    await db.refresh(order)

    event = json.dumps({"order_id": str(order.id), "status": new_status})
    await redis.publish("order.status_changed", event)

    return _order_to_dict(order)


async def cancel_order(
    db: AsyncSession,
    redis: aioredis.Redis,
    http_client: httpx.AsyncClient,
    order_id: str,
    user_id: str,
    inventory_service_url: str,
) -> dict:
    result = await db.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if str(order.user_id) != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if order.status not in (OrderStatus.PENDING, OrderStatus.CONFIRMED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel order with status {order.status}",
        )

    # Release inventory reservations
    for item in order.items:
        try:
            await http_client.post(
                f"{inventory_service_url}/inventory/{item.product_id}/release",
                json={"quantity": item.quantity, "order_id": str(order.id)},
                timeout=10.0,
            )
        except Exception:
            pass  # Best effort

    return await update_status(db, redis, order_id, OrderStatus.CANCELLED, note="Cancelled by customer", changed_by=user_id)


async def get_order_history(db: AsyncSession, order_id: str) -> list[dict]:
    result = await db.execute(
        select(OrderStatusHistory)
        .where(OrderStatusHistory.order_id == uuid.UUID(order_id))
        .order_by(OrderStatusHistory.changed_at.asc())
    )
    history = result.scalars().all()
    return [
        {
            "id": str(h.id),
            "order_id": str(h.order_id),
            "status": h.status,
            "note": h.note,
            "changed_by": str(h.changed_by) if h.changed_by else None,
            "changed_at": h.changed_at.isoformat() if h.changed_at else None,
        }
        for h in history
    ]


async def start_payment_listener(redis_url: str, db_factory) -> None:
    """Background task: listen for payment.completed events."""
    import asyncio
    client = aioredis.from_url(redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe("payment.completed")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    order_id = data.get("order_id")
                    if order_id:
                        async with db_factory() as db:
                            redis_client = aioredis.from_url(redis_url, decode_responses=True)
                            try:
                                await update_status(db, redis_client, order_id, OrderStatus.CONFIRMED, note="Payment confirmed")
                            finally:
                                await redis_client.aclose()
                except Exception:
                    pass
    finally:
        await pubsub.unsubscribe("payment.completed")
        await client.aclose()
