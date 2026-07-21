import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

FAKE_USER = {"sub": str(uuid.uuid4()), "email": "test@example.com", "role": "customer"}
FAKE_ADMIN = {"sub": str(uuid.uuid4()), "email": "admin@example.com", "role": "admin"}
AUTH_HEADERS = {"Authorization": "Bearer fake.token"}


def make_mock_inventory(product_id: str = None):
    pid = product_id or str(uuid.uuid4())
    return {
        "id": str(uuid.uuid4()),
        "product_id": pid,
        "quantity": 100,
        "reserved": 10,
        "available": 90,
        "warehouse_id": "main",
        "low_stock_threshold": 10,
        "updated_at": "2024-01-01T00:00:00",
    }


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "inventory-service"


@pytest.mark.asyncio
async def test_get_inventory():
    product_id = str(uuid.uuid4())
    mock_inv = make_mock_inventory(product_id)
    with (
        patch("app.routers.inventory.get_db"),
        patch("app.services.inventory_service.get_inventory", return_value=mock_inv),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/inventory/{product_id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_set_inventory():
    product_id = str(uuid.uuid4())
    mock_inv = make_mock_inventory(product_id)
    with (
        patch("app.routers.inventory.require_admin", return_value=FAKE_ADMIN),
        patch("app.routers.inventory.get_db"),
        patch("app.routers.inventory.get_redis"),
        patch("app.services.inventory_service.set_inventory", return_value=mock_inv),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/inventory/",
                json={"product_id": product_id, "quantity": 100, "low_stock_threshold": 10},
                headers=AUTH_HEADERS,
            )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_adjust_quantity():
    product_id = str(uuid.uuid4())
    mock_inv = make_mock_inventory(product_id)
    mock_inv["quantity"] = 110
    with (
        patch("app.routers.inventory.require_admin", return_value=FAKE_ADMIN),
        patch("app.routers.inventory.get_db"),
        patch("app.routers.inventory.get_redis"),
        patch("app.services.inventory_service.adjust_quantity", return_value=mock_inv),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.patch(
                f"/inventory/{product_id}",
                json={"quantity_delta": 10, "note": "Restocked"},
                headers=AUTH_HEADERS,
            )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_reserve_stock():
    product_id = str(uuid.uuid4())
    mock_inv = make_mock_inventory(product_id)
    mock_inv["reserved"] = 15
    with (
        patch("app.routers.inventory.get_db"),
        patch("app.services.inventory_service.reserve_stock", return_value=mock_inv),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/inventory/{product_id}/reserve",
                json={"quantity": 5, "order_id": str(uuid.uuid4())},
            )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_release_reservation():
    product_id = str(uuid.uuid4())
    mock_inv = make_mock_inventory(product_id)
    with (
        patch("app.routers.inventory.get_db"),
        patch("app.services.inventory_service.release_reservation", return_value=mock_inv),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/inventory/{product_id}/release",
                json={"quantity": 5, "order_id": str(uuid.uuid4())},
            )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_movements():
    product_id = str(uuid.uuid4())
    with (
        patch("app.routers.inventory.get_current_user", return_value=FAKE_USER),
        patch("app.routers.inventory.get_db"),
        patch("app.services.inventory_service.get_movements", return_value=[]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/inventory/{product_id}/movements", headers=AUTH_HEADERS)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_low_stock():
    with (
        patch("app.routers.inventory.require_admin", return_value=FAKE_ADMIN),
        patch("app.routers.inventory.get_db"),
        patch("app.services.inventory_service.get_low_stock", return_value=[make_mock_inventory()]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/inventory/low-stock", headers=AUTH_HEADERS)
    assert resp.status_code == 200
