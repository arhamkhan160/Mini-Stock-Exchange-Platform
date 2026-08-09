"""Broker consumers: order.accepted and order.cancel_requested.

Idempotency here is IN MEMORY, not database-backed, because the engine has no
database — that is the documented consequence of an in-memory book (proposal
§4). Two layers:
  * `order_id in book.index`  — the order is still resting, so we already saw it
  * `seen_orders`             — a bounded LRU, so a redelivered *fully filled*
                                order (no longer in the index) is not re-matched

Both consumers take the same per-symbol lock, so a cancel can never interleave
with a match. Nothing is published until the book is consistent again: the
match runs to completion inside the lock, and the events go out after it.
"""

import logging
from decimal import Decimal

from common.events import ORDER_CANCEL_REJECTED, ORDER_CANCELLED, TRADE_EXECUTED, utcnow_iso
from common.money import MoneyError, to_money
from common.symbols import normalize_symbol

from .engine import BookOrder, books, cancel, locks, match

log = logging.getLogger(__name__)

# Set by main.lifespan once the connection is up. Read through the module
# global so the late assignment is visible here.
broker = None

SEEN_ORDERS_MAX = 10_000
# dict preserves insertion order, which makes this a two-line bounded LRU.
seen_orders: dict[str, None] = {}

_seq = 0


def _remember(order_id: str) -> None:
    seen_orders[order_id] = None
    while len(seen_orders) > SEEN_ORDERS_MAX:
        seen_orders.pop(next(iter(seen_orders)))


def _parse(p: dict) -> BookOrder | None:
    """Turn an order.accepted payload into a BookOrder, or None if unusable.

    A malformed or unknown-symbol event is logged and DROPPED, never retried:
    it will be just as malformed on the fifth delivery.
    """
    global _seq
    try:
        symbol = normalize_symbol(p.get("symbol"))
    except (ValueError, TypeError):
        log.error("order.accepted for unknown symbol %r, dropping", p.get("symbol"))
        return None

    side = p.get("side")
    order_type = p.get("order_type")
    if side not in ("BUY", "SELL") or order_type not in ("LIMIT", "MARKET"):
        log.error("order.accepted with bad side/type %r/%r, dropping", side, order_type)
        return None

    quantity = p.get("quantity")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        log.error("order.accepted with bad quantity %r, dropping", quantity)
        return None

    price: Decimal | None = None
    if order_type == "LIMIT":
        try:
            price = to_money(p.get("price"))
        except MoneyError:
            log.error("LIMIT order.accepted with bad price %r, dropping", p.get("price"))
            return None

    order_id, user_id = p.get("order_id"), p.get("user_id")
    if not order_id or not user_id:
        log.error("order.accepted missing order_id/user_id, dropping")
        return None

    _seq += 1
    return BookOrder(
        order_id=str(order_id),
        user_id=str(user_id),
        side=side,
        price=price,
        remaining=quantity,
        symbol=symbol,
        seq=_seq,
        order_type=order_type,
    )


async def _publish(routing_key: str, payload: dict) -> None:
    """Publish one event. Never raises.

    ponytail: an in-memory book cannot replay a match, so re-raising here would
    make the retry a no-op (the order is already deduped) while risking a double
    fill if it were not. A publish failure is logged loudly and swallowed.
    Upgrade path if this ever matters: a transactional outbox, which needs the
    database the engine deliberately does not have.
    """
    if broker is None:
        log.error("broker not connected, dropping %s %s", routing_key, payload.get("order_id"))
        return
    try:
        await broker.publish_event(routing_key, payload)
    except Exception:
        log.exception("FAILED to publish %s payload=%s", routing_key, payload)


async def handle_order_accepted(env: dict) -> None:
    order = _parse(env.get("payload") or {})
    if order is None:
        return

    async with locks[order.symbol]:
        book = books[order.symbol]
        if order.order_id in book.index or order.order_id in seen_orders:
            log.info("duplicate order.accepted %s, ignoring", order.order_id)
            return
        _remember(order.order_id)
        trades, cancel_event = match(book, order)

    for trade in trades:
        log.info(
            "trade %s %s %s @ %s",
            trade["symbol"], trade["quantity"], trade["aggressor_side"], trade["price"],
            extra={"trade_id": trade["trade_id"], "symbol": trade["symbol"]},
        )
        await _publish(TRADE_EXECUTED, trade)

    if cancel_event:
        await _publish(ORDER_CANCELLED, cancel_event)


async def handle_cancel_requested(env: dict) -> None:
    p = env.get("payload") or {}
    order_id = p.get("order_id")
    user_id = p.get("user_id")
    try:
        symbol = normalize_symbol(p.get("symbol"))
    except (ValueError, TypeError):
        log.error("order.cancel_requested for unknown symbol %r, dropping", p.get("symbol"))
        return

    async with locks[symbol]:
        removed = cancel(books[symbol], str(order_id))
        remaining = removed.remaining if removed else 0

    if removed is None:
        # The book is the authority. We never publish order.cancelled for
        # something it did not hold, or Account would release funds twice.
        # `seen_orders` is what lets us tell "filled and gone" from "never here".
        reason = "ALREADY_FILLED" if order_id in seen_orders else "NOT_IN_BOOK"
        await _publish(ORDER_CANCEL_REJECTED, {
            "order_id": order_id,
            "user_id": user_id,
            "reason": reason,
            "rejected_at": utcnow_iso(),
        })
        return

    await _publish(ORDER_CANCELLED, {
        "order_id": removed.order_id,
        "user_id": removed.user_id,
        "symbol": symbol,
        "side": removed.side,
        "cancelled_quantity": remaining,
        "reason": "USER_REQUEST",
        "cancelled_at": utcnow_iso(),
    })
