import uuid
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class NotificationType(StrEnum):
    ORDER_CONFIRMED = "order_confirmed"
    ORDER_SHIPPED = "order_shipped"
    ORDER_DELIVERED = "order_delivered"
    ORDER_CANCELLED = "order_cancelled"
    PAYMENT_COMPLETED = "payment_completed"
    PAYMENT_FAILED = "payment_failed"
    LOW_STOCK_ALERT = "low_stock_alert"
    ACCOUNT = "account"


class NotificationChannel(StrEnum):
    EMAIL = "email"
    IN_APP = "in_app"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    type = Column(String(50), nullable=False)
    channel = Column(String(20), nullable=False)
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), default="pending", nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index("ix_notifications_user_id_is_read", "user_id", "is_read"),
    )

    def __repr__(self) -> str:
        return (
            f"<Notification id={self.id} user_id={self.user_id} "
            f"type={self.type} channel={self.channel} is_read={self.is_read}>"
        )
