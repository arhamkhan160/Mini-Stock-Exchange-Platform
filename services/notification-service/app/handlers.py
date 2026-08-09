"""Event handlers.

Every handler is idempotent and non-blocking for the trading path: this service
sits behind the broker precisely so a slow consumer here can never slow down
the matching engine.

Idempotency is two-layered:
  1. Redis SET NX marker (fast, shared)
  2. processed_events primary key (durable, survives a Redis flush)
If the handler raises, the Redis marker is cleared so the broker retry can
actually re-run the work.
"""

import logging
import uuid

from sqlalchemy.exc import IntegrityError

from common.money import to_money
from common.redis_client import mark_event_processed, seen_event

from .deps import SERVICE, SessionLocal, redis
from .emailer import send_mock_email
from .models import (
    MAX_MESSAGE_LEN,
    MAX_TITLE_LEN,
    ORDER_CANCELLED,
    ORDER_FILLED,
    ORDER_PARTIALLY_FILLED,
    ORDER_REJECTED,
    Notification,
    ProcessedEvent,
)

log = logging.getLogger(__name__)


def _human(amount) -> str:
    """4dp wire string -> 2dp human string. Display only."""
    return f"{to_money(amount):.2f}"


def _as_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _build(user_id, ntype: str, title: str, message: str, symbol=None, reference_id=None) -> Notification:
    return Notification(
        user_id=_as_uuid(user_id),
        type=ntype,
        title=title[:MAX_TITLE_LEN],
        message=message[:MAX_MESSAGE_LEN],  # a long reject reason must not break the INSERT
        symbol=symbol,
        reference_id=_as_uuid(reference_id),
    )


async def _handle(env: dict, build_notifications) -> None:
    """Shared idempotency + persistence wrapper.

    `build_notifications(payload)` returns a list of Notification rows.

    Idempotency is database-first. Redis is only a fast path: it is read here
    and written *after* the commit, so a crash mid-handler can never make an
    unprocessed event look processed. The authority is the `processed_events`
    primary key, inserted in the same transaction as the notifications.
    """
    event_id = env.get("event_id")
    event_type = env.get("event_type")
    if await seen_event(redis, SERVICE, event_id):
        log.debug("skipping duplicate event %s", event_id)
        return

    rows = build_notifications(env.get("payload") or {})
    deliverable = []
    for row in rows:
        if row.user_id is None:
            log.error("dropping notification with unusable user_id, event %s", event_id)
        else:
            deliverable.append(row)

    async with SessionLocal() as session:
        for row in deliverable:
            session.add(row)
        marker = _as_uuid(event_id)
        if marker is not None:
            session.add(ProcessedEvent(event_id=marker, event_type=event_type))
        try:
            await session.commit()
        except IntegrityError:
            # processed_events PK collision => another delivery got there
            # first. Not an error; the work is already durable.
            await session.rollback()
            log.debug("event %s already recorded in processed_events", event_id)
            return

    await mark_event_processed(redis, SERVICE, event_id)

    # Mock email is a side effect of already-committed work. It must never
    # fail the handler, or a retry would redo nothing and simply re-fail.
    for row in deliverable:
        try:
            await send_mock_email(row.user_id, row.title, row.message)
        except Exception:
            log.exception("mock email failed for notification %s", row.id)


# --------------------------------------------------------------------------- #
# trade.executed -> one notification per side
# --------------------------------------------------------------------------- #
async def handle_trade_executed(env: dict) -> None:
    def build(p: dict) -> list[Notification]:
        symbol = p.get("symbol")
        qty = int(p.get("quantity", 0))
        price = _human(p.get("price", "0"))
        trade_id = p.get("trade_id")

        rows: list[Notification] = []
        for side, user_key, order_key, remaining_key, verb in (
            ("BUY", "buyer_user_id", "buy_order_id", "buy_order_remaining", "Bought"),
            ("SELL", "seller_user_id", "sell_order_id", "sell_order_remaining", "Sold"),
        ):
            remaining = int(p.get(remaining_key, 0) or 0)
            complete = remaining == 0
            rows.append(
                _build(
                    p.get(user_key),
                    ORDER_FILLED if complete else ORDER_PARTIALLY_FILLED,
                    "Order filled" if complete else "Order partially filled",
                    f"{verb} {qty} {symbol} @ ${price}"
                    + ("" if complete else f" — {remaining} still working"),
                    symbol=symbol,
                    reference_id=trade_id,
                )
            )
        return rows

    await _handle(env, build)


# --------------------------------------------------------------------------- #
# order.cancelled
# --------------------------------------------------------------------------- #
async def handle_order_cancelled(env: dict) -> None:
    def build(p: dict) -> list[Notification]:
        symbol = p.get("symbol")
        qty = int(p.get("cancelled_quantity", 0) or 0)
        reason = p.get("reason", "USER_REQUEST")

        # Nothing actually happened worth telling the user about.
        if reason == "IOC_REMAINDER" and qty == 0:
            return []

        if reason == "NO_LIQUIDITY":
            title = "Order could not be filled"
            message = f"No liquidity available for your market order on {symbol}"
        elif reason == "IOC_REMAINDER":
            title = "Order partially filled"
            message = f"{qty} {symbol} shares could not be filled and were cancelled"
        else:
            title = "Order cancelled"
            message = f"Cancelled {qty} unfilled {symbol} shares"

        return [
            _build(
                p.get("user_id"),
                ORDER_CANCELLED,
                title,
                message,
                symbol=symbol,
                reference_id=p.get("order_id"),
            )
        ]

    await _handle(env, build)


# --------------------------------------------------------------------------- #
# order.rejected
# --------------------------------------------------------------------------- #
async def handle_order_rejected(env: dict) -> None:
    def build(p: dict) -> list[Notification]:
        symbol = p.get("symbol")
        reason = str(p.get("reason") or "Order could not be accepted")
        return [
            _build(
                p.get("user_id"),
                ORDER_REJECTED,
                "Order rejected",
                f"Your {symbol} order was rejected: {reason}",
                symbol=symbol,
                reference_id=p.get("order_id"),
            )
        ]

    await _handle(env, build)
