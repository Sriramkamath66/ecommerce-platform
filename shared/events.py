"""Redis pub/sub event channel names and payload schemas for inter-service communication."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import json


class EventChannel(StrEnum):
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    PRODUCT_DELETED = "product.deleted"
    ORDER_CREATED = "order.created"
    ORDER_STATUS_CHANGED = "order.status_changed"
    ORDER_CONFIRMED = "order.confirmed"
    ORDER_CANCELLED = "order.cancelled"
    PAYMENT_COMPLETED = "payment.completed"
    PAYMENT_FAILED = "payment.failed"
    INVENTORY_LOW_STOCK = "inventory.low_stock"
    INVENTORY_RESERVED = "inventory.reserved"
    INVENTORY_RELEASED = "inventory.released"


def encode_event(channel: str, payload: dict[str, Any]) -> str:
    return json.dumps({"channel": channel, "payload": payload})


def decode_event(raw: str) -> tuple[str, dict[str, Any]]:
    data = json.loads(raw)
    return data["channel"], data["payload"]
