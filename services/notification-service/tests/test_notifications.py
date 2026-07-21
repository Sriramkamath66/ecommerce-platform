"""
Self-contained tests for the notification-service.

No real database, Redis, or SMTP server is needed — every external dependency
is replaced with AsyncMock / MagicMock from unittest.mock.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationChannel, NotificationType
from app.services.event_consumer import CHANNELS, start_consumer
from app.services.notification_service import (
    create_notification,
    get_unread_count,
    get_user_notifications,
    mark_all_read,
    mark_read,
    process_event,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_notification(**overrides) -> MagicMock:
    """Return a MagicMock that looks like a Notification ORM object."""
    n = MagicMock(spec=Notification)
    n.id = overrides.get("id", uuid.uuid4())
    n.user_id = overrides.get("user_id", uuid.uuid4())
    n.type = overrides.get("type", NotificationType.PAYMENT_COMPLETED)
    n.channel = overrides.get("channel", NotificationChannel.IN_APP)
    n.subject = overrides.get("subject", "Test Subject")
    n.body = overrides.get("body", "Test body text.")
    n.is_read = overrides.get("is_read", False)
    n.sent_at = overrides.get("sent_at", None)
    n.status = overrides.get("status", "sent")
    n.error_message = overrides.get("error_message", None)
    n.created_at = overrides.get("created_at", datetime.now(timezone.utc))
    return n


def _make_db() -> AsyncMock:
    """Return an AsyncMock that mimics an AsyncSession."""
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()  # synchronous on the session
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.close = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# test_create_notification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_notification():
    """create_notification should add a Notification to the session and return it."""
    db = _make_db()
    user_id = str(uuid.uuid4())

    result = await create_notification(
        db,
        user_id=user_id,
        type=NotificationType.PAYMENT_COMPLETED,
        channel=NotificationChannel.IN_APP,
        subject="Payment Received",
        body="Your payment has been received.",
    )

    # session.add must have been called exactly once
    db.add.assert_called_once()
    added_obj = db.add.call_args[0][0]
    assert isinstance(added_obj, Notification)

    # flush and refresh must have been awaited
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(added_obj)

    # The returned object is the same instance that was added
    assert result is added_obj

    # Check the fields we explicitly set
    assert added_obj.type == NotificationType.PAYMENT_COMPLETED
    assert added_obj.channel == NotificationChannel.IN_APP
    assert added_obj.subject == "Payment Received"
    assert added_obj.body == "Your payment has been received."
    assert added_obj.status == "sent"
    assert added_obj.is_read is False
    assert added_obj.sent_at is not None


# ---------------------------------------------------------------------------
# test_get_user_notifications_pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_notifications_pagination():
    """get_user_notifications should respect page / page_size and return total."""
    user_id = str(uuid.uuid4())
    notification_1 = _make_notification(user_id=uuid.UUID(user_id))
    notification_2 = _make_notification(user_id=uuid.UUID(user_id))

    db = _make_db()

    # scalar() → total count
    db.scalar = AsyncMock(return_value=42)

    # execute() → paginated rows
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [notification_1, notification_2]
    db.execute = AsyncMock(return_value=mock_result)

    notifications, total = await get_user_notifications(
        db, user_id, unread_only=False, page=3, page_size=2
    )

    assert total == 42
    assert len(notifications) == 2
    assert notifications[0] is notification_1
    assert notifications[1] is notification_2

    # execute must have been called (for the main SELECT)
    db.execute.assert_awaited()


@pytest.mark.asyncio
async def test_get_user_notifications_unread_only():
    """When unread_only=True only unread notifications are queried."""
    user_id = str(uuid.uuid4())

    db = _make_db()
    db.scalar = AsyncMock(return_value=5)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=mock_result)

    _, total = await get_user_notifications(db, user_id, unread_only=True)

    assert total == 5
    # Both scalar (count) and execute (SELECT) must have been called
    db.scalar.assert_awaited_once()
    db.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# test_mark_read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_read():
    """mark_read should set is_read=True on the found notification and return it."""
    user_id = str(uuid.uuid4())
    notification_id = str(uuid.uuid4())

    mock_notification = _make_notification(
        id=uuid.UUID(notification_id),
        user_id=uuid.UUID(user_id),
        is_read=False,
    )

    db = _make_db()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_notification
    db.execute = AsyncMock(return_value=mock_result)

    result = await mark_read(db, notification_id, user_id)

    assert result is mock_notification
    assert mock_notification.is_read is True
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(mock_notification)


@pytest.mark.asyncio
async def test_mark_read_not_found():
    """mark_read should return None when the notification does not exist."""
    db = _make_db()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=mock_result)

    result = await mark_read(db, str(uuid.uuid4()), str(uuid.uuid4()))

    assert result is None
    db.flush.assert_not_awaited()


# ---------------------------------------------------------------------------
# test_mark_all_read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_all_read():
    """mark_all_read should return the count of rows updated."""
    user_id = str(uuid.uuid4())

    db = _make_db()
    mock_result = MagicMock()
    # Simulate 3 rows returned by RETURNING clause
    mock_result.fetchall.return_value = [MagicMock(), MagicMock(), MagicMock()]
    db.execute = AsyncMock(return_value=mock_result)

    count = await mark_all_read(db, user_id)

    assert count == 3
    db.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# test_get_unread_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_unread_count():
    """get_unread_count should return the scalar count from the database."""
    user_id = str(uuid.uuid4())

    db = _make_db()
    db.scalar = AsyncMock(return_value=7)

    count = await get_unread_count(db, user_id)

    assert count == 7
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_unread_count_none_becomes_zero():
    """get_unread_count should return 0 when the DB returns None."""
    user_id = str(uuid.uuid4())

    db = _make_db()
    db.scalar = AsyncMock(return_value=None)

    count = await get_unread_count(db, user_id)

    assert count == 0


# ---------------------------------------------------------------------------
# test_process_event_payment_completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_event_payment_completed():
    """process_event for 'payment.completed' should send a receipt email and
    create an IN_APP notification."""
    user_id = str(uuid.uuid4())
    order_id = str(uuid.uuid4())
    amount = 149.99

    # Mock HTTP client — returns a user record with an email address
    mock_http_response = MagicMock()
    mock_http_response.status_code = 200
    mock_http_response.json.return_value = {"id": user_id, "email": "customer@example.com"}
    mock_http_response.raise_for_status = MagicMock()
    http_client = AsyncMock()
    http_client.get = AsyncMock(return_value=mock_http_response)

    # Mock email service
    email_service = AsyncMock()
    email_service.send_payment_receipt = AsyncMock(return_value=True)

    # Mock DB session
    db = _make_db()

    payload = {"user_id": user_id, "order_id": order_id, "amount": amount}

    await process_event(
        db, None, email_service, http_client, "payment.completed", payload
    )

    # Email receipt must have been sent to the fetched address
    email_service.send_payment_receipt.assert_awaited_once_with(
        "customer@example.com", order_id, amount
    )

    # A notification row must have been added to the session
    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert isinstance(added, Notification)
    assert added.type == NotificationType.PAYMENT_COMPLETED
    assert added.channel == NotificationChannel.IN_APP


@pytest.mark.asyncio
async def test_process_event_payment_completed_no_email():
    """When the user-service is unavailable, no email is sent but the
    in-app notification is still created."""
    user_id = str(uuid.uuid4())
    order_id = str(uuid.uuid4())

    # HTTP client raises an exception (user-service down)
    http_client = AsyncMock()
    http_client.get = AsyncMock(side_effect=Exception("connection refused"))

    email_service = AsyncMock()
    email_service.send_payment_receipt = AsyncMock(return_value=True)

    db = _make_db()
    payload = {"user_id": user_id, "order_id": order_id, "amount": 50.0}

    # Should not raise
    await process_event(
        db, None, email_service, http_client, "payment.completed", payload
    )

    # Email must NOT have been attempted
    email_service.send_payment_receipt.assert_not_awaited()

    # But the in-app notification must still be persisted
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_process_event_payment_failed():
    """process_event for 'payment.failed' sends a failure email and creates
    a PAYMENT_FAILED in-app notification."""
    user_id = str(uuid.uuid4())
    order_id = str(uuid.uuid4())
    reason = "Insufficient funds"

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": user_id, "email": "buyer@example.com"}
    mock_response.raise_for_status = MagicMock()
    http_client = AsyncMock()
    http_client.get = AsyncMock(return_value=mock_response)

    email_service = AsyncMock()
    email_service.send_payment_failed = AsyncMock(return_value=True)

    db = _make_db()
    payload = {"user_id": user_id, "order_id": order_id, "reason": reason}

    await process_event(
        db, None, email_service, http_client, "payment.failed", payload
    )

    email_service.send_payment_failed.assert_awaited_once_with(
        "buyer@example.com", order_id, reason
    )

    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert added.type == NotificationType.PAYMENT_FAILED


@pytest.mark.asyncio
async def test_process_event_order_status_shipped():
    """order.status_changed with new_status='shipped' triggers email and
    an ORDER_SHIPPED in-app notification."""
    user_id = str(uuid.uuid4())
    order_id = str(uuid.uuid4())

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": user_id, "email": "shopper@example.com"}
    mock_response.raise_for_status = MagicMock()
    http_client = AsyncMock()
    http_client.get = AsyncMock(return_value=mock_response)

    email_service = AsyncMock()
    email_service.send_order_status_changed = AsyncMock(return_value=True)

    db = _make_db()
    payload = {
        "user_id": user_id,
        "order_id": order_id,
        "old_status": "processing",
        "new_status": "shipped",
    }

    await process_event(
        db, None, email_service, http_client, "order.status_changed", payload
    )

    email_service.send_order_status_changed.assert_awaited_once_with(
        "shopper@example.com", order_id, "processing", "shipped"
    )

    db.add.assert_called_once()
    assert db.add.call_args[0][0].type == NotificationType.ORDER_SHIPPED


@pytest.mark.asyncio
async def test_process_event_order_status_no_notification_for_processing():
    """order.status_changed with new_status='processing' does NOT create
    any notification because that status is not in the trigger list."""
    http_client = AsyncMock()
    email_service = AsyncMock()
    db = _make_db()

    payload = {
        "user_id": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "old_status": "pending",
        "new_status": "processing",
    }

    await process_event(
        db, None, email_service, http_client, "order.status_changed", payload
    )

    db.add.assert_not_called()
    http_client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_event_inventory_low_stock():
    """inventory.low_stock fetches all admin users and sends each an alert."""
    product_id = str(uuid.uuid4())
    admin_id_1 = str(uuid.uuid4())
    admin_id_2 = str(uuid.uuid4())

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = [
        {"id": admin_id_1, "email": "admin1@example.com"},
        {"id": admin_id_2, "email": "admin2@example.com"},
    ]
    http_client = AsyncMock()
    http_client.get = AsyncMock(return_value=mock_response)

    email_service = AsyncMock()
    email_service.send_low_stock_alert = AsyncMock(return_value=True)

    db = _make_db()
    payload = {"product_id": product_id, "quantity": 3}

    await process_event(
        db, None, email_service, http_client, "inventory.low_stock", payload
    )

    assert email_service.send_low_stock_alert.await_count == 2
    assert db.add.call_count == 2

    for call in db.add.call_args_list:
        added = call[0][0]
        assert added.type == NotificationType.LOW_STOCK_ALERT


# ---------------------------------------------------------------------------
# test_event_consumer_subscribes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_consumer_subscribes():
    """start_consumer must subscribe to every channel in CHANNELS before
    starting to listen for messages."""
    subscribed_channels: list[str] = []

    async def mock_subscribe(*channels: str) -> None:
        subscribed_channels.extend(channels)
        # Raise CancelledError to exit the consumer after the first subscribe call
        raise asyncio.CancelledError("test teardown")

    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = mock_subscribe

    mock_redis = MagicMock()
    mock_redis.pubsub.return_value = mock_pubsub

    app_state = MagicMock()
    app_state.redis = mock_redis

    with pytest.raises(asyncio.CancelledError):
        await start_consumer(app_state)

    assert set(subscribed_channels) == set(CHANNELS), (
        f"Expected channels {sorted(CHANNELS)}, "
        f"got {sorted(subscribed_channels)}"
    )


@pytest.mark.asyncio
async def test_event_consumer_reconnects_on_error():
    """start_consumer must catch non-CancelledError exceptions, sleep, and retry.

    We inject a RuntimeError on the first subscribe call and a CancelledError
    on the second so the loop runs exactly twice before terminating.
    """
    call_count = 0

    async def mock_subscribe(*_channels: str) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated connection error")
        raise asyncio.CancelledError("test teardown")

    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = mock_subscribe

    mock_redis = MagicMock()
    mock_redis.pubsub.return_value = mock_pubsub

    app_state = MagicMock()
    app_state.redis = mock_redis

    # Patch asyncio.sleep so the test doesn't actually wait 5 seconds
    with patch("app.services.event_consumer.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(asyncio.CancelledError):
            await start_consumer(app_state)

    assert call_count == 2, "Consumer should have retried exactly once after the error"
