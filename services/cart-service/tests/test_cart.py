import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


def make_mock_redis(items: dict | None = None, meta: dict | None = None):
    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(side_effect=lambda key: items if "meta" not in key else (meta or {}))
    mock_redis.hget = AsyncMock(return_value=None)
    mock_redis.hset = AsyncMock(return_value=1)
    mock_redis.hdel = AsyncMock(return_value=1)
    mock_redis.delete = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.aclose = AsyncMock()
    return mock_redis


FAKE_USER = {"sub": "user-123", "email": "test@example.com", "role": "customer"}
FAKE_TOKEN = "Bearer fake.jwt.token"


@pytest.fixture
def auth_headers():
    return {"Authorization": FAKE_TOKEN}


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "cart-service"


@pytest.mark.asyncio
async def test_get_empty_cart(auth_headers):
    mock_redis = make_mock_redis(items={}, meta={})
    with (
        patch("app.routers.cart.get_redis", return_value=mock_redis),
        patch("app.dependencies.get_current_user", return_value=FAKE_USER),
        patch("app.routers.cart.get_current_user", return_value=FAKE_USER),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/cart/", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["subtotal"] == 0.0


@pytest.mark.asyncio
async def test_add_item(auth_headers):
    mock_redis = make_mock_redis(items={}, meta={})
    with (
        patch("app.routers.cart.get_redis", return_value=mock_redis),
        patch("app.routers.cart.get_current_user", return_value=FAKE_USER),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/cart/items",
                json={"product_id": "prod-1", "quantity": 2},
                headers=auth_headers,
            )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_apply_valid_coupon(auth_headers):
    mock_redis = make_mock_redis(items={}, meta={})
    with (
        patch("app.routers.cart.get_redis", return_value=mock_redis),
        patch("app.routers.cart.get_current_user", return_value=FAKE_USER),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/cart/apply-coupon",
                json={"coupon_code": "SAVE10"},
                headers=auth_headers,
            )
    assert resp.status_code == 200
    assert resp.json()["discount_pct"] == 10


@pytest.mark.asyncio
async def test_apply_invalid_coupon(auth_headers):
    mock_redis = make_mock_redis(items={}, meta={})
    with (
        patch("app.routers.cart.get_redis", return_value=mock_redis),
        patch("app.routers.cart.get_current_user", return_value=FAKE_USER),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/cart/apply-coupon",
                json={"coupon_code": "INVALID"},
                headers=auth_headers,
            )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_clear_cart(auth_headers):
    mock_redis = make_mock_redis(items={"prod-1": "2"}, meta={})
    with (
        patch("app.routers.cart.get_redis", return_value=mock_redis),
        patch("app.routers.cart.get_current_user", return_value=FAKE_USER),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/cart/", headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_remove_item(auth_headers):
    mock_redis = make_mock_redis(items={"prod-1": "2"}, meta={})
    with (
        patch("app.routers.cart.get_redis", return_value=mock_redis),
        patch("app.routers.cart.get_current_user", return_value=FAKE_USER),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/cart/items/prod-1", headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_checkout_empty_cart(auth_headers):
    mock_redis = make_mock_redis(items={}, meta={})
    with (
        patch("app.routers.cart.get_redis", return_value=mock_redis),
        patch("app.routers.cart.get_current_user", return_value=FAKE_USER),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/cart/checkout",
                json={"shipping_address": {"street": "123 Main St", "city": "NYC", "state": "NY", "zip": "10001", "country": "US"}},
                headers=auth_headers,
            )
    assert resp.status_code == 400
