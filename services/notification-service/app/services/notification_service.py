import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.models.notification import Notification, NotificationChannel, NotificationType
from app.services.email_service import EmailService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------


async def create_notification(
    db: AsyncSession,
    user_id: str,
    type: str,  # noqa: A002
    channel: str,
    subject: str,
    body: str,
) -> Notification:
    """Persist a new notification and return the refreshed ORM object."""
    notification = Notification(
        id=uuid.uuid4(),
        user_id=uuid.UUID(str(user_id)),
        type=type,
        channel=channel,
        subject=subject,
        body=body,
        is_read=False,
        status="sent",
        sent_at=datetime.now(timezone.utc),
    )
    db.add(notification)
    await db.flush()
    await db.refresh(notification)
    return notification


async def get_user_notifications(
    db: AsyncSession,
    user_id: str,
    unread_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Notification], int]:
    """Return a paginated list of notifications and the total count."""
    user_uuid = uuid.UUID(str(user_id))

    where_clauses = [Notification.user_id == user_uuid]
    if unread_only:
        where_clauses.append(Notification.is_read == False)  # noqa: E712

    total: int = await db.scalar(
        select(func.count(Notification.id)).where(*where_clauses)
    ) or 0

    result = await db.execute(
        select(Notification)
        .where(*where_clauses)
        .order_by(Notification.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    notifications = list(result.scalars().all())
    return notifications, total


async def mark_read(
    db: AsyncSession,
    notification_id: str,
    user_id: str,
) -> Optional[Notification]:
    """Mark a single notification as read; returns None if not found or not owned."""
    user_uuid = uuid.UUID(str(user_id))
    notification_uuid = uuid.UUID(str(notification_id))

    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_uuid,
            Notification.user_id == user_uuid,
        )
    )
    notification = result.scalar_one_or_none()
    if notification is None:
        return None

    notification.is_read = True
    await db.flush()
    await db.refresh(notification)
    return notification


async def mark_all_read(db: AsyncSession, user_id: str) -> int:
    """Mark every unread notification for *user_id* as read.

    Returns the number of rows updated.
    """
    user_uuid = uuid.UUID(str(user_id))
    result = await db.execute(
        update(Notification)
        .where(
            Notification.user_id == user_uuid,
            Notification.is_read == False,  # noqa: E712
        )
        .values(is_read=True)
        .returning(Notification.id)
    )
    rows = result.fetchall()
    return len(rows)


async def get_unread_count(db: AsyncSession, user_id: str) -> int:
    """Return the number of unread notifications for *user_id*."""
    user_uuid = uuid.UUID(str(user_id))
    count: int = await db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_uuid,
            Notification.is_read == False,  # noqa: E712
        )
    ) or 0
    return count


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_user_email(
    http_client: httpx.AsyncClient, user_id: str
) -> Optional[str]:
    """GET the user record from the user-service and extract the email field."""
    url = f"{app_settings.USER_SERVICE_URL}/users/{user_id}"
    try:
        response = await http_client.get(url, timeout=10.0)
        response.raise_for_status()
        return response.json().get("email")
    except Exception as exc:
        logger.error("Failed to fetch user %s from user-service: %s", user_id, exc)
        return None


# ---------------------------------------------------------------------------
# Event processor
# ---------------------------------------------------------------------------

_STATUS_TO_TYPE: dict[str, NotificationType] = {
    "shipped": NotificationType.ORDER_SHIPPED,
    "delivered": NotificationType.ORDER_DELIVERED,
    "cancelled": NotificationType.ORDER_CANCELLED,
}


async def process_event(
    db: AsyncSession,
    redis,
    email_svc: EmailService,
    http_client: httpx.AsyncClient,
    channel: str,
    payload: dict,
) -> None:
    """Route an incoming pub/sub event to the appropriate handler."""

    if channel == "payment.completed":
        user_id: str = str(payload.get("user_id", ""))
        order_id: str = str(payload.get("order_id", ""))
        amount: float = float(payload.get("amount", 0.0))

        email = await _fetch_user_email(http_client, user_id)
        if email:
            await email_svc.send_payment_receipt(email, order_id, amount)

        await create_notification(
            db,
            user_id,
            NotificationType.PAYMENT_COMPLETED,
            NotificationChannel.IN_APP,
            "Payment Completed",
            f"Your payment of ${amount:.2f} for order #{order_id} has been processed successfully.",
        )

    elif channel == "payment.failed":
        user_id = str(payload.get("user_id", ""))
        order_id = str(payload.get("order_id", ""))
        reason: str = str(payload.get("reason", "Unknown error"))

        email = await _fetch_user_email(http_client, user_id)
        if email:
            await email_svc.send_payment_failed(email, order_id, reason)

        await create_notification(
            db,
            user_id,
            NotificationType.PAYMENT_FAILED,
            NotificationChannel.IN_APP,
            "Payment Failed",
            f"Payment for order #{order_id} could not be processed. Reason: {reason}",
        )

    elif channel == "order.status_changed":
        user_id = str(payload.get("user_id", ""))
        order_id = str(payload.get("order_id", ""))
        old_status: str = str(payload.get("old_status", ""))
        new_status: str = str(payload.get("new_status", ""))

        if new_status in _STATUS_TO_TYPE:
            email = await _fetch_user_email(http_client, user_id)
            if email:
                await email_svc.send_order_status_changed(
                    email, order_id, old_status, new_status
                )

            await create_notification(
                db,
                user_id,
                _STATUS_TO_TYPE[new_status],
                NotificationChannel.IN_APP,
                f"Order {new_status.capitalize()}",
                f"Your order #{order_id} status has changed from "
                f"{old_status} to {new_status}.",
            )
        else:
            logger.debug(
                "order.status_changed: new_status=%s does not trigger notification",
                new_status,
            )

    elif channel == "inventory.low_stock":
        product_id: str = str(payload.get("product_id", ""))
        quantity: int = int(payload.get("quantity", 0))

        try:
            response = await http_client.get(
                f"{app_settings.USER_SERVICE_URL}/users",
                params={"role": "admin"},
                timeout=10.0,
            )
            response.raise_for_status()
            admins: list[dict] = response.json()
        except Exception as exc:
            logger.error(
                "inventory.low_stock: failed to fetch admin users: %s", exc
            )
            admins = []

        for admin in admins:
            admin_email: Optional[str] = admin.get("email")
            admin_id: Optional[str] = str(admin.get("id", "")) or None

            if admin_email:
                await email_svc.send_low_stock_alert(
                    admin_email, product_id, quantity
                )

            if admin_id:
                await create_notification(
                    db,
                    admin_id,
                    NotificationType.LOW_STOCK_ALERT,
                    NotificationChannel.IN_APP,
                    "Low Stock Alert",
                    f"Product {product_id} has only {quantity} unit(s) remaining.",
                )

    else:
        logger.warning("process_event: unrecognised channel %s", channel)
