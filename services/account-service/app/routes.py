import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.money import to_money
from common.security import CurrentUser, get_current_user, require_internal_key

from .deps import get_session, redis
from .locking import FundsLockUnavailable, funds_lock
from .models import DEPOSIT, HELD, HOLD, RELEASE, Account, Reservation, Transaction
from .repo import get_or_create_account, release_remaining
from .schemas import (
    Balance,
    DepositRequest,
    ReservationOut,
    ReservationReleaseOut,
    ReservationRequest,
    TransactionResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["account"])

MAX_TX_LIMIT = 200


def _balance_out(account: Account) -> Balance:
    available = account.cash_balance - account.held_balance
    return Balance(
        user_id=str(account.user_id),
        cash_balance=str(account.cash_balance),
        held_balance=str(account.held_balance),
        available_balance=str(available),
        currency=account.currency,
    )


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field} must be a UUID")


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
        async with funds_lock(redis, user.id):
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
    except FundsLockUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="try again")


@router.get("/account/transactions", response_model=list[TransactionResponse])
async def list_transactions(
    limit: int = Query(50, ge=1, le=MAX_TX_LIMIT),
    offset: int = Query(0, ge=0),
    type: str | None = Query(None, description="filter by transaction type, e.g. DEPOSIT"),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Transaction).where(Transaction.user_id == user.id)
    if type:
        stmt = stmt.where(Transaction.type == type.upper())
    stmt = stmt.order_by(Transaction.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------- #
# Internal — reservations and account lookup. Never reachable through the
# gateway (it refuses to proxy any path containing /internal/), so
# X-Internal-Key is the only guard.
# --------------------------------------------------------------------------- #
@router.get(
    "/internal/accounts/{user_id}",
    response_model=Balance,
    dependencies=[Depends(require_internal_key)],
)
async def internal_get_account(user_id: str, session: AsyncSession = Depends(get_session)):
    uid = _parse_uuid(user_id, "user_id")
    try:
        async with funds_lock(redis, uid):
            # Same lock as every other first-touch — without it, two
            # concurrent lookups for a brand-new user_id could both try to
            # INSERT the same account row.
            account = await get_or_create_account(session, uid)
            await session.commit()  # persist a get-or-create's implicit new row
            return _balance_out(account)
    except FundsLockUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="try again")


@router.post(
    "/internal/reservations",
    response_model=ReservationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_internal_key)],
)
async def reserve_funds(body: ReservationRequest, session: AsyncSession = Depends(get_session)):
    order_id = _parse_uuid(body.order_id, "order_id")
    user_id = _parse_uuid(body.user_id, "user_id")
    amount = to_money(body.amount)

    try:
        async with funds_lock(redis, user_id):
            # Idempotent on order_id: a retried call after a dropped response
            # must not double-reserve. Rows are never deleted, so this also
            # catches a retry arriving after the reservation has since moved
            # to CONSUMED/RELEASED — it just echoes back the original hold.
            existing = (
                await session.execute(select(Reservation).where(Reservation.order_id == order_id))
            ).scalars().first()
            if existing is not None:
                return ReservationOut(
                    order_id=str(order_id),
                    user_id=str(existing.user_id),
                    amount_held=str(existing.amount_held),
                    status=existing.status,
                )

            account = await get_or_create_account(session, user_id)
            available = account.cash_balance - account.held_balance
            if available < amount:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="insufficient buying power")

            account.held_balance += amount
            session.add(Reservation(order_id=order_id, user_id=user_id, amount_held=amount, status=HELD))
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
            return ReservationOut(order_id=str(order_id), user_id=str(user_id), amount_held=str(amount), status=HELD)
    except FundsLockUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="try again")


@router.post(
    "/internal/reservations/{order_id}/release",
    response_model=ReservationReleaseOut,
    dependencies=[Depends(require_internal_key)],
)
async def release_reservation(order_id: str, session: AsyncSession = Depends(get_session)):
    oid = _parse_uuid(order_id, "order_id")

    reservation = (
        await session.execute(select(Reservation).where(Reservation.order_id == oid))
    ).scalars().first()
    if reservation is None:
        # 404 only here: the order_id was never reserved at all. An
        # already-resolved reservation is a 200 with released="0.0000" below —
        # release must be safe to call more than once.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no reservation for this order_id")

    remaining = reservation.remaining
    if remaining <= 0:
        return ReservationReleaseOut(order_id=order_id, released="0.0000")

    user_id = reservation.user_id
    try:
        async with funds_lock(redis, user_id):
            account = await get_or_create_account(session, user_id)
            released = release_remaining(reservation, account)
            if released > 0:
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
            return ReservationReleaseOut(order_id=order_id, released=str(released))
    except FundsLockUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="try again")
