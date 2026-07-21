import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.schemas.notification import (
    NotificationListResponse,
    NotificationResponse,
    UnreadCountResponse,
)
from app.services import notification_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get(
    "/",
    response_model=NotificationListResponse,
    summary="List the current user's notifications",
)
async def list_notifications(
    unread_only: bool = Query(False, description="Return only unread notifications"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> NotificationListResponse:
    notifications, total = await notification_service.get_user_notifications(
        db, user_id, unread_only=unread_only, page=page, page_size=page_size
    )
    return NotificationListResponse(
        notifications=notifications,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Get the number of unread notifications for the current user",
)
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> UnreadCountResponse:
    count = await notification_service.get_unread_count(db, user_id)
    return UnreadCountResponse(count=count)


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark a specific notification as read",
)
async def mark_notification_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> NotificationResponse:
    notification = await notification_service.mark_read(
        db, str(notification_id), user_id
    )
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found or does not belong to you",
        )
    return notification  # type: ignore[return-value]


@router.patch(
    "/read-all",
    summary="Mark all of the current user's notifications as read",
    response_description="Number of notifications marked as read",
)
async def mark_all_notifications_read(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    count = await notification_service.mark_all_read(db, user_id)
    return {"marked": count}
