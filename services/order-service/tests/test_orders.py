import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

FAKE_USER = {"sub": str(uuid.uuid4()), "email": "test@example.com", "role": "customer"}
FAKE_ADMIN = {"sub": str(uuid.uuid4()), "email": "admin@example.com", "role": "admin"}
AUTH_HEADERS = {"Authorization": "Bearer fake.token"}


def make_mock_order():
    return {
        "id": str(uuid.uuid4()),
        "user_id": FAKE_USER["sub"],
        "status": "pending",
        "subtotal": 100.0,
        "discount": 0.0,
        "shipping_fee": 0.0,
        "total": 100.0,
        "shipping_address": {"street": "123 Main", "city": "NYC", "state": "NY", "zip": "10001", "country": "US"},
        "coupon_code": None,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
        "items": [],
    }


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "order-service"


@pytest.mark.asyncio
async def test_create_order():
    mock_order = make_mock_order()
    with (
        patch("app.routers.orders.get_current_user", return_value=FAKE_USER),
        patch("app.routers.orders.get_db"),
        patch("app.routers.orders.get_redis"),
        patch("app.services.order_service.create_order", return_value=mock_order),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/orders/",
                json={
                    "items": [{"product_id": str(uuid.uuid4()), "product_name": "Widget", "quantity": 1, "unit_price": 100.0, "total_price": 100.0}],
                    "shipping_address": {"street": "123 Main", "city": "NYC", "state": "NY", "zip": "10001", "country": "US"},
                    "subtotal": 100.0,
                    "total": 100.0,
                },
                headers=AUTH_HEADERS,
            )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_list_orders():
    with (
        patch("app.routers.orders.get_current_user", return_value=FAKE_USER),
        patch("app.routers.orders.get_db"),
        patch("app.services.order_service.list_orders", return_value=[make_mock_order()]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/orders/", headers=AUTH_HEADERS)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_order():
    mock_order = make_mock_order()
    order_id = mock_order["id"]
    with (
        patch("app.routers.orders.get_current_user", return_value=FAKE_USER),
        patch("app.routers.orders.get_db"),
        patch("app.services.order_service.get_order", return_value=mock_order),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/orders/{order_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_update_status_admin():
    mock_order = make_mock_order()
    mock_order["status"] = "confirmed"
    order_id = mock_order["id"]
    with (
        patch("app.routers.orders.require_admin", return_value=FAKE_ADMIN),
        patch("app.routers.orders.get_db"),
        patch("app.routers.orders.get_redis"),
        patch("app.services.order_service.update_status", return_value=mock_order),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.patch(
                f"/orders/{order_id}/status",
                json={"status": "confirmed", "note": "Payment received"},
                headers=AUTH_HEADERS,
            )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_cancel_order():
    mock_order = make_mock_order()
    mock_order["status"] = "cancelled"
    order_id = mock_order["id"]
    with (
        patch("app.routers.orders.get_current_user", return_value=FAKE_USER),
        patch("app.routers.orders.get_db"),
        patch("app.routers.orders.get_redis"),
        patch("app.routers.orders.get_http_client"),
        patch("app.services.order_service.cancel_order", return_value=mock_order),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/orders/{order_id}/cancel", headers=AUTH_HEADERS)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_history():
    order_id = str(uuid.uuid4())
    history = [{"id": str(uuid.uuid4()), "order_id": order_id, "status": "pending", "note": "Created", "changed_by": None, "changed_at": "2024-01-01T00:00:00"}]
    with (
        patch("app.routers.orders.get_current_user", return_value=FAKE_USER),
        patch("app.routers.orders.get_db"),
        patch("app.services.order_service.get_order_history", return_value=history),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/orders/{order_id}/history", headers=AUTH_HEADERS)
    assert resp.status_code == 200
