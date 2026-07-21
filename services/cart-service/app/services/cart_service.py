import json
import time
from typing import Any
from uuid import UUID

import httpx
import redis.asyncio as aioredis

CART_TTL = 30 * 24 * 60 * 60  # 30 days in seconds

COUPON_MAP = {
    "SAVE10": 10,
    "SAVE20": 20,
}


def _cart_key(user_id: str) -> str:
    return f"cart:{user_id}"


def _meta_key(user_id: str) -> str:
    return f"cart:{user_id}:meta"


async def _reset_ttl(redis: aioredis.Redis, user_id: str) -> None:
    await redis.expire(_cart_key(user_id), CART_TTL)
    await redis.expire(_meta_key(user_id), CART_TTL)


async def _fetch_product(http_client: httpx.AsyncClient, product_id: str, product_service_url: str) -> dict:
    try:
        resp = await http_client.get(f"{product_service_url}/products/{product_id}")
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {"id": product_id, "name": "Unknown Product", "price": 0.0}


async def get_cart(
    redis: aioredis.Redis,
    user_id: str,
    http_client: httpx.AsyncClient,
    product_service_url: str,
) -> dict:
    raw_items = await redis.hgetall(_cart_key(user_id))
    meta = await redis.hgetall(_meta_key(user_id))
    await _reset_ttl(redis, user_id)

    items = []
    subtotal = 0.0
    for product_id, qty_str in raw_items.items():
        quantity = int(qty_str)
        product_data = await _fetch_product(http_client, product_id, product_service_url)
        price = float(product_data.get("price", 0.0))
        line_total = price * quantity
        subtotal += line_total
        items.append({
            "product_id": product_id,
            "quantity": quantity,
            "product_data": product_data,
            "unit_price": price,
            "line_total": line_total,
        })

    discount_pct = float(meta.get("discount_pct", 0))
    coupon_code = meta.get("coupon_code")
    discount = round(subtotal * discount_pct / 100, 2)
    total = round(subtotal - discount, 2)

    return {
        "items": items,
        "subtotal": round(subtotal, 2),
        "coupon_code": coupon_code,
        "discount_pct": discount_pct,
        "discount": discount,
        "total": total,
        "item_count": len(items),
    }


async def add_item(redis: aioredis.Redis, user_id: str, product_id: str, quantity: int) -> None:
    if quantity <= 0:
        raise ValueError("Quantity must be positive")
    existing = await redis.hget(_cart_key(user_id), product_id)
    new_qty = (int(existing) if existing else 0) + quantity
    await redis.hset(_cart_key(user_id), product_id, str(new_qty))
    await _reset_ttl(redis, user_id)


async def update_item(redis: aioredis.Redis, user_id: str, product_id: str, quantity: int) -> None:
    if quantity <= 0:
        await remove_item(redis, user_id, product_id)
    else:
        await redis.hset(_cart_key(user_id), product_id, str(quantity))
        await _reset_ttl(redis, user_id)


async def remove_item(redis: aioredis.Redis, user_id: str, product_id: str) -> None:
    await redis.hdel(_cart_key(user_id), product_id)
    await _reset_ttl(redis, user_id)


async def clear_cart(redis: aioredis.Redis, user_id: str) -> None:
    await redis.delete(_cart_key(user_id), _meta_key(user_id))


async def apply_coupon(redis: aioredis.Redis, user_id: str, coupon_code: str) -> dict:
    code = coupon_code.upper().strip()
    if code not in COUPON_MAP:
        raise ValueError(f"Invalid coupon code: {coupon_code}")
    discount_pct = COUPON_MAP[code]
    expires_at = int(time.time()) + CART_TTL
    await redis.hset(_meta_key(user_id), mapping={
        "coupon_code": code,
        "discount_pct": str(discount_pct),
        "expires_at": str(expires_at),
    })
    await _reset_ttl(redis, user_id)
    return {"coupon_code": code, "discount_pct": discount_pct, "message": f"Coupon applied: {discount_pct}% off"}


async def checkout(
    redis: aioredis.Redis,
    http_client: httpx.AsyncClient,
    user_id: str,
    shipping_address: dict,
    product_service_url: str,
    order_service_url: str,
    inventory_service_url: str,
) -> dict:
    raw_items = await redis.hgetall(_cart_key(user_id))
    meta = await redis.hgetall(_meta_key(user_id))

    if not raw_items:
        raise ValueError("Cart is empty")

    # Enrich items with product data
    order_items = []
    subtotal = 0.0
    for product_id, qty_str in raw_items.items():
        quantity = int(qty_str)
        product_data = await _fetch_product(http_client, product_id, product_service_url)
        price = float(product_data.get("price", 0.0))
        line_total = price * quantity
        subtotal += line_total
        order_items.append({
            "product_id": product_id,
            "product_name": product_data.get("name", "Unknown"),
            "quantity": quantity,
            "unit_price": price,
            "total_price": line_total,
        })

    discount_pct = float(meta.get("discount_pct", 0))
    coupon_code = meta.get("coupon_code")
    discount = round(subtotal * discount_pct / 100, 2)
    total = round(subtotal - discount, 2)

    # Reserve inventory for each item
    reservations = []
    for item in order_items:
        try:
            resp = await http_client.post(
                f"{inventory_service_url}/inventory/{item['product_id']}/reserve",
                json={"quantity": item["quantity"], "order_id": None},
                timeout=10.0,
            )
            if resp.status_code not in (200, 201):
                # Release already reserved items
                for r in reservations:
                    await http_client.post(
                        f"{inventory_service_url}/inventory/{r['product_id']}/release",
                        json={"quantity": r["quantity"], "order_id": None},
                    )
                raise ValueError(f"Insufficient stock for product {item['product_id']}")
            reservations.append(item)
        except httpx.RequestError:
            raise ValueError("Inventory service unavailable")

    # Create order
    order_payload = {
        "user_id": user_id,
        "items": order_items,
        "shipping_address": shipping_address,
        "subtotal": subtotal,
        "discount": discount,
        "total": total,
        "coupon_code": coupon_code,
    }
    try:
        resp = await http_client.post(f"{order_service_url}/orders/", json=order_payload, timeout=15.0)
        if resp.status_code not in (200, 201):
            raise ValueError(f"Order creation failed: {resp.text}")
        order_data = resp.json()
    except httpx.RequestError:
        raise ValueError("Order service unavailable")

    # Clear cart on success
    await clear_cart(redis, user_id)

    return {
        "order_id": order_data.get("id"),
        "total": total,
        "status": order_data.get("status", "pending"),
        "message": "Order placed successfully",
    }
