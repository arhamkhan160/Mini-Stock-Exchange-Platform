import logging
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.money import to_money
from common.redis_client import LockTimeout, distributed_lock
from common.security import CurrentUser, get_current_user, require_internal_key

from .deps import get_session, redis
from .models import DEPOSIT, HOLD, RELEASE, Account, Reservation, Transaction
from .repo import get_or_create_account
from .schemas import (
    Balance,
    DepositRequest,
    ReservationOut,
    ReservationReleaseOut,
    ReservationReleaseRequest,
    ReservationRequest,
    TransactionResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["account"])

ZERO = Decimal("0.0000")
MAX_TX_LIMIT = 200
LOCK_WAIT_SECONDS = 2.0


def _balance_out(account: Account) -> Balance:
    available = account.cash_balance - account.held_balance
    return Balance(
        user_id=str(account.user_id),
        cash_balance=str(account.cash_balance),
        held_balance=str(account.held_balance),
        available_balance=str(available),
    )


@router.get("/account/balance", response_model=Balance)
async def get_balance(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    account = await get_or_create_account(session, user.id)
    return _balance_out(account)


@router.post("/account/deposit", response_model=Balance)
async def deposit(
    body: DepositRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    amount = to_money(body.amount)
    try:
        async with distributed_lock(redis, f"lock:funds:{user.id}", wait_seconds=LOCK_WAIT_SECONDS):
            account = await get_or_create_account(session, user.id)
            account.cash_balance += amount
            session.add(
                Transaction(
                    user_id=user.id,
                    type=DEPOSIT,
                    amount=amount,
                    balance_after=account.cash_balance,
                    description="User deposit",
                )
            )
            await session.commit()
            await session.refresh(account)
            return _balance_out(account)
    except LockTimeout:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="try again")


@router.get("/account/transactions", response_model=list[TransactionResponse])
async def list_transactions(
    limit: int = Query(50, ge=1, le=MAX_TX_LIMIT),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------- #
# Internal — reservations. Never reachable through the gateway (it refuses to
# proxy any path containing /internal/), so X-Internal-Key is the only guard.
# --------------------------------------------------------------------------- #
@router.post(
    "/internal/reservations",
    response_model=ReservationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_internal_key)],
)
async def reserve_funds(body: ReservationRequest, session: AsyncSession = Depends(get_session)):
    try:
        order_id = uuid.UUID(body.order_id)
        user_id = uuid.UUID(body.user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="order_id/user_id must be UUIDs")
    amount = to_money(body.amount)

    try:
        async with distributed_lock(redis, f"lock:funds:{user_id}", wait_seconds=LOCK_WAIT_SECONDS):
            # Idempotent on order_id: a retried call after a dropped response
            # must not double-reserve.
            existing = (
                await session.execute(select(Reservation).where(Reservation.order_id == order_id))
            ).scalars().first()
            if existing is not None:
                return ReservationOut(order_id=str(order_id), user_id=str(existing.user_id), amount=str(existing.amount), status="HELD")

            account = await get_or_create_account(session, user_id)
            available = account.cash_balance - account.held_balance
            if available < amount:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="insufficient buying power")

            account.held_balance += amount
            session.add(Reservation(order_id=order_id, user_id=user_id, amount=amount))
            session.add(
                Transaction(
                    user_id=user_id,
                    type=HOLD,
                    amount=-amount,
                    balance_after=account.cash_balance,
                    reference_id=order_id,
                    description="Funds held for order",
                )
            )
            await session.commit()
            return ReservationOut(order_id=str(order_id), user_id=str(user_id), amount=str(amount), status="HELD")
    except LockTimeout:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="try again")


@router.post(
    "/internal/reservations/{order_id}/release",
    response_model=ReservationReleaseOut,
    dependencies=[Depends(require_internal_key)],
)
async def release_reservation(
    order_id: str,
    body: ReservationReleaseRequest = ReservationReleaseRequest(),
    session: AsyncSession = Depends(get_session),
):
    try:
        oid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="order_id must be a UUID")

    reservation = (
        await session.execute(select(Reservation).where(Reservation.order_id == oid))
    ).scalars().first()
    if reservation is None:
        # Already released (or never held, e.g. a SELL order) — release is
        # idempotent, so this is a success, not a 404.
        return ReservationReleaseOut(order_id=order_id, released_amount="0.0000", remaining_amount="0.0000")

    user_id = reservation.user_id
    requested = to_money(body.amount) if body.amount is not None else reservation.amount

    try:
        async with distributed_lock(redis, f"lock:funds:{user_id}", wait_seconds=LOCK_WAIT_SECONDS):
            released = min(requested, reservation.amount)
            account = await get_or_create_account(session, user_id)
            account.held_balance = max(ZERO, account.held_balance - released)
            reservation.amount -= released
            remaining = reservation.amount
            if remaining <= ZERO:
                await session.delete(reservation)
                remaining = ZERO
            if released > ZERO:
                session.add(
                    Transaction(
                        user_id=user_id,
                        type=RELEASE,
                        amount=released,
                        balance_after=account.cash_balance,
                        reference_id=oid,
                        description="Reservation released",
                    )
                )
            await session.commit()
            return ReservationReleaseOut(
                order_id=order_id, released_amount=str(released), remaining_amount=str(remaining)
            )
    except LockTimeout:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="try again")
