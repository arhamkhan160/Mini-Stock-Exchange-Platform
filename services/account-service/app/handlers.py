"""Event handlers.

Every handler is idempotent and non-blocking for the trading path — this
service sits behind the broker precisely so a slow consumer here never slows
down the matching engine.

Idempotency is database-first (see common.redis_client.seen_event for the
full rationale): Redis is read before the work and written only after the
commit that also inserts the `processed_events` row. A raising handler is
retried by common.events.Broker with no special handling needed here —
including a FundsLockUnavailable from app.locking, which must NOT be
swallowed: settling money without the lock held is not safe, so the broker's
retry-with-backoff is exactly the right response to Redis being down.

Locking: `lock:funds:{user_id}` (via app.locking.funds_lock, which also logs
every acquire/release) guards every read-modify-write of an Account/
Reservation row, matching the REST endpoints in routes.py so a reservation
and a concurrent settlement for the same user can never race. RabbitMQ
delivers each queue to this service with prefetch=1, so at most one event
handler runs at a time — the only remaining hazard is a single trade.executed
where the buyer and seller are the same user, handled by locking the *set* of
distinct user ids rather than each side separately (locking the same key
twice from one task would deadlock against itself).
"""

import logging
import uuid
from contextlib import AsyncExitStack
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from common.money import notional, to_money
from common.redis_client import mark_event_processed, seen_event

from .deps import SERVICE, SessionLocal, redis
from .locking import funds_lock
from .models import RELEASE, TRADE_BUY, TRADE_SELL, ProcessedEvent, Reservation, Transaction
from .repo import get_or_create_account, release_remaining

log = logging.getLogger(__name__)

ZERO = Decimal("0.0000")


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _drop_unprocessable(event_id: str | None) -> None:
    """A payload we can never act on (bad ids). Mark it handled so the retry
    loop does not spin on it forever, and move on — a poison message must not
    wedge the queue."""
    await mark_event_processed(redis, SERVICE, event_id)


# --------------------------------------------------------------------------- #
# trade.executed -> settle both sides, consume/release the buyer's hold
# --------------------------------------------------------------------------- #
async def handle_trade_executed(env: dict) -> None:
    event_id = env.get("event_id")
    event_type = env.get("event_type")
    if await seen_event(redis, SERVICE, event_id):
        log.debug("skipping duplicate trade.executed %s", event_id)
        return

    payload = env.get("payload") or {}
    buyer_id = _uuid(payload.get("buyer_user_id"))
    seller_id = _uuid(payload.get("seller_user_id"))
    buy_order_id = _uuid(payload.get("buy_order_id"))
    trade_id = _uuid(payload.get("trade_id"))

    if buyer_id is None or seller_id is None:
        log.error("trade.executed with unusable user id(s), event %s — dropping", event_id)
        await _drop_unprocessable(event_id)
        return

    symbol = payload.get("symbol")
    price = to_money(payload["price"])
    quantity = int(payload["quantity"])
    actual_cost = notional(price, quantity)

    limit_price_raw = payload.get("buy_order_limit_price")
    # MARKET buy orders have no limit price, so there is no "price improvement"
    # to release for them — the whole reservation is assumed consumed at cost.
    reserved_price = to_money(limit_price_raw) if limit_price_raw else price
    buy_order_remaining = int(payload.get("buy_order_remaining", 0) or 0)

    marker = _uuid(event_id)

    async with SessionLocal() as session:
        async with AsyncExitStack() as locks:
            for uid in sorted({buyer_id, seller_id}, key=str):
                await locks.enter_async_context(funds_lock(redis, uid))

            # ---- buyer: pay, and consume/release the matching reservation ----
            buyer = await get_or_create_account(session, buyer_id)

            reservation = None
            if buy_order_id is not None:
                res_stmt = select(Reservation).where(Reservation.order_id == buy_order_id).with_for_update()
                reservation = (await session.execute(res_stmt)).scalars().first()

            if reservation is not None:
                remaining_hold = max(ZERO, reservation.remaining)
                consumed = min(notional(reserved_price, quantity), remaining_hold)
                reservation.amount_consumed += consumed
            else:
                # Should not happen — Order Service must reserve before an
                # order can ever reach the book. Defensive fallback: debit
                # whatever cash is available rather than crash the handler.
                log.error("settling a buy with no reservation, order %s event %s", buy_order_id, event_id)
                consumed = min(actual_cost, buyer.cash_balance)

            buyer.cash_balance -= actual_cost
            buyer.held_balance = max(ZERO, buyer.held_balance - consumed)

            session.add(
                Transaction(
                    user_id=buyer_id,
                    type=TRADE_BUY,
                    amount=-actual_cost,
                    balance_after=buyer.cash_balance,
                    reference_id=trade_id,
                    description=f"Bought {quantity} {symbol} @ {price}",
                )
            )

            if buy_order_remaining == 0 and reservation is not None:
                # Order is done — release whatever slack is left (always zero
                # for an exact LIMIT fill, can be positive for a MARKET order
                # or price improvement not yet reclaimed by an earlier fill).
                release_remaining(reservation, buyer)

            # ---- seller: receive proceeds. Shares were held by Portfolio,
            # not Account, so there is no cash reservation to touch here. ----
            seller = await get_or_create_account(session, seller_id)
            seller.cash_balance += actual_cost
            session.add(
                Transaction(
                    user_id=seller_id,
                    type=TRADE_SELL,
                    amount=actual_cost,
                    balance_after=seller.cash_balance,
                    reference_id=trade_id,
                    description=f"Sold {quantity} {symbol} @ {price}",
                )
            )

            if marker is not None:
                session.add(ProcessedEvent(event_id=marker, event_type=event_type))
            try:
                await session.commit()
            except IntegrityError:
                # processed_events PK collision => another delivery got there first.
                await session.rollback()
                log.debug("event %s already recorded in processed_events", event_id)
                return

    await mark_event_processed(redis, SERVICE, event_id)


# --------------------------------------------------------------------------- #
# order.cancelled / order.rejected -> release whatever is left of the hold
# --------------------------------------------------------------------------- #
async def _release_reservation(env: dict, source: str) -> None:
    event_id = env.get("event_id")
    event_type = env.get("event_type")
    if await seen_event(redis, SERVICE, event_id):
        log.debug("skipping duplicate %s %s", source, event_id)
        return

    payload = env.get("payload") or {}
    order_id = _uuid(payload.get("order_id"))
    user_id = _uuid(payload.get("user_id"))
    marker = _uuid(event_id)

    if order_id is None or user_id is None:
        log.error("%s with unusable id(s), event %s — dropping", source, event_id)
        await _drop_unprocessable(event_id)
        return

    async with SessionLocal() as session:
        async with funds_lock(redis, user_id):
            res_stmt = select(Reservation).where(Reservation.order_id == order_id).with_for_update()
            reservation = (await session.execute(res_stmt)).scalars().first()
            if reservation is not None:
                # A SELL order (or one that never held cash) simply has no row
                # here — nothing to release, and that is not an error.
                account = await get_or_create_account(session, user_id)
                released = release_remaining(reservation, account)
                if released > ZERO:
                    session.add(
                        Transaction(
                            user_id=user_id,
                            type=RELEASE,
                            amount=released,
                            balance_after=account.cash_balance,
                            reference_id=order_id,
                            description=f"Hold released ({source})",
                        )
                    )

            if marker is not None:
                session.add(ProcessedEvent(event_id=marker, event_type=event_type))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                log.debug("event %s already recorded in processed_events", event_id)
                return

    await mark_event_processed(redis, SERVICE, event_id)


async def handle_order_cancelled(env: dict) -> None:
    await _release_reservation(env, "order.cancelled")


async def handle_order_rejected(env: dict) -> None:
    await _release_reservation(env, "order.rejected")
