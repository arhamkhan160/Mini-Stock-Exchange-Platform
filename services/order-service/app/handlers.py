"""Event consumers: trade.executed, order.cancelled, order.cancel_rejected.

Idempotency is DATABASE-FIRST. The authority is the `processed_events` primary
key, inserted in the SAME transaction as the work. Redis is only a fast path:
READ before the work, WRITTEN after the commit. Claiming the event first (SET
NX) would mean a crash between the claim and the commit makes the event look
handled forever, and it is silently lost. A read-only check can only cause a
redundant retry, which the primary key stops. Prefer a duplicate over a loss.

Two fills for the same order can arrive back to back, so every order row is
taken with SELECT ... FOR UPDATE before it is touched.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.events import ORDER_ACCEPTED, utcnow_iso
from common.money import money_str, to_money
from common.redis_client import mark_event_processed, seen_event

from .deps import SERVICE, SessionLocal, redis
from .models import (
    CANCEL_PENDING,
    CANCELLED,
    FILLED,
    NEW,
    OPEN,
    PARTIALLY_FILLED,
    PENDING,
    REJECTED,
    TERMINAL,
    Order,
    ProcessedEvent,
)
from .service import publish, release, transition

log = logging.getLogger(__name__)

# A PENDING order older than this crashed between "persisted" and "reserved".
STALE_PENDING_SECONDS = 60


def _as_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _locked(session: AsyncSession, order_id) -> Order | None:
    oid = _as_uuid(order_id)
    if oid is None:
        return None
    return await session.scalar(select(Order).where(Order.id == oid).with_for_update())


async def _once(env: dict, work) -> None:
    """Run `work(session, payload)` exactly once, ever.

    `work` must not commit — the ProcessedEvent row and the work have to land in
    one transaction or the guarantee is worthless.
    """
    event_id = env.get("event_id")
    if await seen_event(redis, SERVICE, event_id):
        return

    async with SessionLocal() as session:
        await work(session, env.get("payload") or {})
        marker = _as_uuid(event_id)
        if marker is not None:
            session.add(ProcessedEvent(event_id=marker, event_type=env.get("event_type")))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()      # another delivery won the race
            return

    await mark_event_processed(redis, SERVICE, event_id)


# --------------------------------------------------------------------------- #
# trade.executed
# --------------------------------------------------------------------------- #
async def handle_trade_executed(env: dict) -> None:
    async def work(session: AsyncSession, p: dict) -> None:
        quantity = int(p.get("quantity") or 0)
        price = to_money(p.get("price") or 0)
        if quantity <= 0:
            log.error("trade.executed with quantity %r, ignoring", p.get("quantity"))
            return

        for order_id, _remaining in (
            (p.get("buy_order_id"), p.get("buy_order_remaining")),
            (p.get("sell_order_id"), p.get("sell_order_remaining")),
        ):
            order = await _locked(session, order_id)
            if order is None:
                # The other side of the trade belongs to someone else's service
                # instance, or the id is junk. Warn and carry on — never nack.
                log.warning("trade names order %s which we do not have", order_id)
                continue

            new_filled = order.filled_quantity + quantity
            if new_filled > order.quantity:
                # Would violate ck_fill_bounds and blow up the whole transaction.
                log.error(
                    "overfill on order %s: %s + %s > %s, clamping",
                    order.id, order.filled_quantity, quantity, order.quantity,
                    extra={"order_id": str(order.id)},
                )
                new_filled = order.quantity
            if new_filled <= 0:
                continue                                   # cannot happen; guard the division

            order.avg_fill_price = to_money(
                (order.avg_fill_price * order.filled_quantity + price * quantity) / new_filled
            )
            order.filled_quantity = new_filled

            if order.status in TERMINAL:
                # A partially filled order that was already cancelled still gets
                # its numbers updated, but CANCELLED is where it stays.
                log.info("fill recorded on already-%s order %s", order.status, order.id)
                continue
            await transition(
                session,
                order,
                FILLED if new_filled == order.quantity else PARTIALLY_FILLED,
                note=f"filled {quantity} @ {money_str(price)}",
            )

    await _once(env, work)


# --------------------------------------------------------------------------- #
# order.cancelled  (published by the MATCHING ENGINE — the book is the authority)
# --------------------------------------------------------------------------- #
async def handle_order_cancelled(env: dict) -> None:
    async def work(session: AsyncSession, p: dict) -> None:
        order = await _locked(session, p.get("order_id"))
        if order is None:
            log.warning("order.cancelled for unknown order %s", p.get("order_id"))
            return
        await transition(
            session, order, CANCELLED,
            note=f"{p.get('reason', 'USER_REQUEST')} ({p.get('cancelled_quantity')} unfilled)",
        )

    await _once(env, work)


# --------------------------------------------------------------------------- #
# order.cancel_rejected — the order was NOT in the book, so put it back
# --------------------------------------------------------------------------- #
async def handle_cancel_rejected(env: dict) -> None:
    async def work(session: AsyncSession, p: dict) -> None:
        order = await _locked(session, p.get("order_id"))
        if order is None:
            log.warning("order.cancel_rejected for unknown order %s", p.get("order_id"))
            return
        if order.status != CANCEL_PENDING:
            return                                       # already resolved by a fill

        if order.filled_quantity >= order.quantity:
            restored = FILLED
        elif order.filled_quantity > 0:
            restored = PARTIALLY_FILLED
        else:
            restored = NEW
        await transition(session, order, restored, note=f"cancel rejected: {p.get('reason')}")

    await _once(env, work)


# --------------------------------------------------------------------------- #
# startup reconciliation (§3.7.2)
# --------------------------------------------------------------------------- #
async def startup_reconciliation() -> None:
    """Clean up whatever the last crash left behind. Never fatal.

    Two holes, both closed here:
      * PENDING for more than a minute — we died between persisting the order
        and reserving. Release (idempotent, safe if nothing was held) and reject.
      * NEW / PARTIALLY_FILLED — the engine may never have seen it (it holds the
        book in memory). Republish order.accepted; the engine dedupes by
        order_id, so a redundant republish costs nothing.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_PENDING_SECONDS)
    try:
        async with SessionLocal() as session:
            stale = (await session.scalars(
                select(Order).where(Order.status == PENDING, Order.created_at < cutoff)
            )).all()
            for order in stale:
                await release(order.id)
                await transition(session, order, REJECTED, note="abandoned PENDING at startup")
                order.reject_reason = "SERVICE_RESTART"
            if stale:
                await session.commit()
                log.warning("reconciliation rejected %s abandoned PENDING order(s)", len(stale))

        async with SessionLocal() as session:
            open_orders = (await session.scalars(
                select(Order).where(Order.status.in_(OPEN)).order_by(Order.created_at)
            )).all()
            for order in open_orders:
                remaining = order.quantity - order.filled_quantity
                if remaining <= 0:
                    continue
                # `quantity` is the REMAINING size here: the engine must not
                # re-book the part that already traded.
                await publish(ORDER_ACCEPTED, {
                    "order_id": str(order.id),
                    "user_id": str(order.user_id),
                    "symbol": order.symbol,
                    "side": order.side,
                    "order_type": order.order_type,
                    "price": money_str(order.price) if order.price is not None else None,
                    "quantity": remaining,
                    "created_at": utcnow_iso(),
                })
            if open_orders:
                log.info("reconciliation republished %s open order(s)", len(open_orders))
    except Exception:
        log.exception("startup reconciliation failed; continuing anyway")
