import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.security import CurrentUser, get_current_user, require_internal_key

from .deps import get_session
from .models import User
from .schemas import UserResponse, UserUpdate

router = APIRouter(tags=["users"])

MAX_BATCH_IDS = 100


@router.get("/users/me", response_model=UserResponse)
async def get_me(user: CurrentUser = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(User).where(User.id == user.id))).scalars().first()
    if row is None:
        # The JWT is valid but the account is gone — a stale token, not a bug.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return row


@router.patch("/users/me", response_model=UserResponse)
async def update_me(
    body: UserUpdate,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    row = (await session.execute(select(User).where(User.id == user.id))).scalars().first()
    if row is None or not row.is_active:
        # Mutating a gone-or-disabled account: force a fresh login rather
        # than confirm which of the two is true.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    if body.full_name is not None:
        row.full_name = body.full_name
    if body.username is not None:
        row.username = body.username

    try:
        await session.commit()
    except IntegrityError:
        # The functional unique index on lower(username) is the real guard
        # against a case-insensitive collision, not a pre-check SELECT.
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="username already taken")
    await session.refresh(row)
    return row


@router.get(
    "/internal/users/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(require_internal_key)],
)
async def internal_get_user(user_id: str, session: AsyncSession = Depends(get_session)):
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    row = (await session.execute(select(User).where(User.id == uid))).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return row


@router.get(
    "/internal/users",
    response_model=list[UserResponse],
    dependencies=[Depends(require_internal_key)],
)
async def internal_get_users_batch(
    ids: str = Query(..., description="comma-separated user ids, max 100"),
    session: AsyncSession = Depends(get_session),
):
    raw_ids = [part.strip() for part in ids.split(",") if part.strip()]
    if len(raw_ids) > MAX_BATCH_IDS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"at most {MAX_BATCH_IDS} ids per call")

    uuids: list[uuid.UUID] = []
    for raw in raw_ids:
        try:
            uuids.append(uuid.UUID(raw))
        except ValueError:
            continue  # a malformed id in the batch just can't match anything — skip, don't fail the whole call

    if not uuids:
        return []
    rows = (await session.execute(select(User).where(User.id.in_(uuids)))).scalars().all()
    return list(rows)
