from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.security import CurrentUser, get_current_user

from .deps import get_session
from .models import Notification
from .schemas import MarkedResult, NotificationOut, UnreadCount

router = APIRouter(tags=["notifications"])

MAX_LIMIT = 200


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/notifications/unread-count", response_model=UnreadCount)
async def unread_count(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
    )
    return UnreadCount(count=(await session.execute(stmt)).scalar_one())


@router.post("/notifications/read-all", response_model=MarkedResult)
async def mark_all_read(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    # One UPDATE, never a loop — a bot user can hold thousands of rows.
    stmt = (
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    result = await session.execute(stmt)
    await session.commit()
    return MarkedResult(marked=result.rowcount or 0)


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Notification).where(
        Notification.id == notification_id,
        # Scoped to the caller: someone else's id yields 404, which does not
        # confirm whether it exists.
        Notification.user_id == user.id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    if not row.is_read:  # idempotent
        row.is_read = True
        await session.commit()
        await session.refresh(row)
    return row
