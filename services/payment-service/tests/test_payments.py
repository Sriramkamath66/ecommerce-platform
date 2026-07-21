import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

FAKE_USER = {"sub": str(uuid.uuid4()), "email": "test@example.com", "role": "customer"}
FAKE_ADMIN = {"sub": str(uuid.uuid4()), "email": "admin@example.com", "role": "admin"}
AUTH_HEADERS = {"Authorization": "Bearer fake.token"}


def make_mock_payment(status: str = "pending"):
    return {
        "id": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "user_id": FAKE_USER["sub"],
        "amount": 99.99,
        "currency": "USD",
        "status": status,
        "method": "card",
        "provider_ref": "pi_" + "a" * 32,
        "failure_reason": None,
        "metadata": {},
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
        "refunds": [],
    }


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "payment-service"


@pytest.mark.asyncio
async def test_initiate_payment():
    mock_payment = make_mock_payment("pending")
    with (
        patch("app.routers.payments.get_current_user", return_value=FAKE_USER),
        patch("app.routers.payments.get_db"),
        patch("app.routers.payments.get_redis"),
        patch("app.services.payment_service.initiate_payment", return_value=mock_payment),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/payments/",
                json={"order_id": str(uuid.uuid4()), "amount": 99.99, "method": "card"},
                headers=AUTH_HEADERS,
            )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_get_payment():
    mock_payment = make_mock_payment()
    payment_id = mock_payment["id"]
    with (
        patch("app.routers.payments.get_current_user", return_value=FAKE_USER),
        patch("app.routers.payments.get_db"),
        patch("app.services.payment_service.get_payment", return_value=mock_payment),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/payments/{payment_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_payments():
    with (
        patch("app.routers.payments.get_current_user", return_value=FAKE_USER),
        patch("app.routers.payments.get_db"),
        patch("app.services.payment_service.list_payments", return_value=[make_mock_payment()]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/payments/", headers=AUTH_HEADERS)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_confirm_payment():
    mock_payment = make_mock_payment("completed")
    payment_id = mock_payment["id"]
    with (
        patch("app.routers.payments.get_current_user", return_value=FAKE_USER),
        patch("app.routers.payments.get_db"),
        patch("app.routers.payments.get_redis"),
        patch("app.services.payment_service.confirm_payment", return_value=mock_payment),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/payments/{payment_id}/confirm", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_webhook_succeeded():
    mock_payment = make_mock_payment("completed")
    with (
        patch("app.routers.payments.get_db"),
        patch("app.routers.payments.get_redis"),
        patch("app.services.payment_service.process_webhook", return_value=mock_payment),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/payments/webhook",
                json={
                    "type": "payment_intent.succeeded",
                    "data": {"object": {"id": "pi_" + "a" * 32}},
                },
            )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_refund_payment():
    mock_payment = make_mock_payment("refunded")
    payment_id = mock_payment["id"]
    with (
        patch("app.routers.payments.get_current_user", return_value=FAKE_USER),
        patch("app.routers.payments.get_db"),
        patch("app.routers.payments.get_redis"),
        patch("app.services.payment_service.create_refund", return_value=mock_payment),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/payments/{payment_id}/refund",
                json={"amount": 99.99, "reason": "Customer request"},
                headers=AUTH_HEADERS,
            )
    assert resp.status_code == 200
    assert resp.json()["status"] == "refunded"
