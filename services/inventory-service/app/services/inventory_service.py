import json
import uuid
from typing import Optional

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import Inventory, StockMovement


def _inv_to_dict(inv: Inventory) -> dict:
    return {
        "id": str(inv.id),
        "product_id": str(inv.product_id),
        "quantity": inv.quantity,
        "reserved": inv.reserved,
        "available": inv.quantity - inv.reserved,
        "warehouse_id": inv.warehouse_id,
        "low_stock_threshold": inv.low_stock_threshold,
        "updated_at": inv.updated_at.isoformat() if inv.updated_at else None,
    }


async def get_inventory(db: AsyncSession, product_id: str) -> dict:
    result = await db.execute(select(Inventory).where(Inventory.product_id == uuid.UUID(product_id)))
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory not found")
    return _inv_to_dict(inv)


async def set_inventory(
    db: AsyncSession,
    redis: aioredis.Redis,
    product_id: str,
    quantity: int,
    threshold: int = 10,
    warehouse_id: str = "main",
) -> dict:
    pid = uuid.UUID(product_id)
    result = await db.execute(select(Inventory).where(Inventory.product_id == pid))
    inv = result.scalar_one_or_none()
    if inv:
        inv.quantity = quantity
        inv.low_stock_threshold = threshold
        inv.warehouse_id = warehouse_id
    else:
        inv = Inventory(
            id=uuid.uuid4(),
            product_id=pid,
            quantity=quantity,
            reserved=0,
            warehouse_id=warehouse_id,
            low_stock_threshold=threshold,
        )
        db.add(inv)
    await db.commit()
    await db.refresh(inv)

    if inv.quantity - inv.reserved <= inv.low_stock_threshold:
        await redis.publish("inventory.low_stock", json.dumps({"product_id": product_id, "available": inv.quantity - inv.reserved}))

    return _inv_to_dict(inv)


async def adjust_quantity(
    db: AsyncSession,
    redis: aioredis.Redis,
    product_id: str,
    delta: int,
    reference_id: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    pid = uuid.UUID(product_id)
    result = await db.execute(select(Inventory).where(Inventory.product_id == pid))
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory not found")

    new_qty = inv.quantity + delta
    if new_qty < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient stock")

    inv.quantity = new_qty
    movement = StockMovement(
        id=uuid.uuid4(),
        product_id=pid,
        movement_type="in" if delta > 0 else "out",
        quantity=abs(delta),
        reference_id=uuid.UUID(reference_id) if reference_id else None,
        note=note,
    )
    db.add(movement)
    await db.commit()
    await db.refresh(inv)

    available = inv.quantity - inv.reserved
    if available <= inv.low_stock_threshold:
        await redis.publish("inventory.low_stock", json.dumps({"product_id": product_id, "available": available}))

    return _inv_to_dict(inv)


async def reserve_stock(
    db: AsyncSession,
    product_id: str,
    quantity: int,
    order_id: Optional[str] = None,
) -> dict:
    pid = uuid.UUID(product_id)
    result = await db.execute(select(Inventory).where(Inventory.product_id == pid))
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory not found")

    available = inv.quantity - inv.reserved
    if available < quantity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Insufficient available stock. Available: {available}, Requested: {quantity}",
        )

    inv.reserved += quantity
    movement = StockMovement(
        id=uuid.uuid4(),
        product_id=pid,
        movement_type="reserve",
        quantity=quantity,
        reference_id=uuid.UUID(order_id) if order_id else None,
        note=f"Reserved for order {order_id}",
    )
    db.add(movement)
    await db.commit()
    await db.refresh(inv)
    return _inv_to_dict(inv)


async def release_reservation(
    db: AsyncSession,
    product_id: str,
    quantity: int,
    order_id: Optional[str] = None,
) -> dict:
    pid = uuid.UUID(product_id)
    result = await db.execute(select(Inventory).where(Inventory.product_id == pid))
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory not found")

    inv.reserved = max(0, inv.reserved - quantity)
    movement = StockMovement(
        id=uuid.uuid4(),
        product_id=pid,
        movement_type="release",
        quantity=quantity,
        reference_id=uuid.UUID(order_id) if order_id else None,
        note=f"Released reservation for order {order_id}",
    )
    db.add(movement)
    await db.commit()
    await db.refresh(inv)
    return _inv_to_dict(inv)


async def confirm_deduction(
    db: AsyncSession,
    product_id: str,
    quantity: int,
) -> dict:
    pid = uuid.UUID(product_id)
    result = await db.execute(select(Inventory).where(Inventory.product_id == pid))
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory not found")

    inv.quantity = max(0, inv.quantity - quantity)
    inv.reserved = max(0, inv.reserved - quantity)
    movement = StockMovement(
        id=uuid.uuid4(),
        product_id=pid,
        movement_type="out",
        quantity=quantity,
        note="Confirmed deduction after order confirmed",
    )
    db.add(movement)
    await db.commit()
    await db.refresh(inv)
    return _inv_to_dict(inv)


async def get_low_stock(db: AsyncSession) -> list[dict]:
    result = await db.execute(select(Inventory))
    items = result.scalars().all()
    return [_inv_to_dict(inv) for inv in items if (inv.quantity - inv.reserved) <= inv.low_stock_threshold]


async def get_movements(db: AsyncSession, product_id: str) -> list[dict]:
    pid = uuid.UUID(product_id)
    result = await db.execute(
        select(StockMovement).where(StockMovement.product_id == pid).order_by(StockMovement.created_at.desc())
    )
    movements = result.scalars().all()
    return [
        {
            "id": str(m.id),
            "product_id": str(m.product_id),
            "movement_type": m.movement_type,
            "quantity": m.quantity,
            "reference_id": str(m.reference_id) if m.reference_id else None,
            "note": m.note,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in movements
    ]


async def start_order_event_listener(redis_url: str, db_factory) -> None:
    """Listen for order.confirmed and order.cancelled events."""
    import asyncio
    client = aioredis.from_url(redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe("order.confirmed", "order.cancelled")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    channel = message["channel"]
                    async with db_factory() as db:
                        if channel == "order.confirmed":
                            # Confirm deduction for each item in the order
                            items = data.get("items", [])
                            for item in items:
                                try:
                                    await confirm_deduction(db, str(item["product_id"]), item["quantity"])
                                except Exception:
                                    pass
                        elif channel == "order.cancelled":
                            items = data.get("items", [])
                            order_id = data.get("order_id")
                            for item in items:
                                try:
                                    await release_reservation(db, str(item["product_id"]), item["quantity"], order_id)
                                except Exception:
                                    pass
                except Exception:
                    pass
    finally:
        await pubsub.unsubscribe("order.confirmed", "order.cancelled")
        await client.aclose()
