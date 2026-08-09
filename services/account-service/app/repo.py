"""Small persistence helpers shared by routes.py and handlers.py."""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CONSUMED, RELEASED, Account, Reservation

ZERO = Decimal("0.0000")


async def get_or_create_account(session: AsyncSession, user_id: uuid.UUID) -> Account:
    """Locks the row (or the gap, via INSERT) for the rest of the caller's
    transaction — every caller holds `lock:funds:{user_id}` around this too,
    but the row lock is what actually stops two Postgres transactions from
    both reading a stale balance."""
    stmt = select(Account).where(Account.user_id == user_id).with_for_update()
    account = (await session.execute(stmt)).scalars().first()
    if account is None:
        account = Account(user_id=user_id)
        session.add(account)
        await session.flush()
    return account


def release_remaining(reservation: Reservation, account: Account) -> Decimal:
    """Releases whatever is left of a reservation back to available_balance
    and advances its terminal status. Safe to call on an already-resolved
    reservation (remaining <= 0) — it is then a no-op returning 0.

    Shared by: the finishing fill of an order (trade.executed with
    buy_order_remaining == 0, which may still have slack — MARKET orders,
    or the last fill of several), order.cancelled/order.rejected, and the
    REST release endpoint.
    """
    remaining = reservation.remaining
    if remaining > ZERO:
        account.held_balance = max(ZERO, account.held_balance - remaining)
        reservation.amount_released += remaining
    reservation.status = RELEASED if reservation.amount_released > ZERO else CONSUMED
    return max(ZERO, remaining)
