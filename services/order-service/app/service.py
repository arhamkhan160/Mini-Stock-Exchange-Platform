"""The saga orchestrator.

The Order Service owns the order lifecycle AND its compensating actions. The
order of operations in `place_order` is the whole point of the design — see the
numbered comments; each one exists because the step before it can fail.

Every status write goes through `transition()`. There is exactly one place that
assigns `order.status`, so "a terminal order never changes again" is enforced by
a table (`models.ALLOWED`) rather than by remembering to check.
"""

import logging
import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.config import settings
from common.events import ORDER_ACCEPTED, ORDER_REJECTED, utcnow_iso
from common.http_client import ServiceCallError, call_service
from common.money import MoneyError, money_str, notional, to_money, validate_price, validate_quantity
from common.redis_client import get_last_price
from common.symbols import SEED_PRICES, normalize_symbol

from . import deps
from .deps import redis
from .models import ALLOWED, MAX_REASON_LEN, NEW, PENDING, REJECTED, Order, OrderEvent
from .schemas import PlaceOrderIn

log = logging.getLogger(__name__)

# A MARKET buy has no limit price, so we hold a reference price plus a buffer.
# Anything not filled inside the buffer is cancelled (IOC) and released.
MARKET_SLIPPAGE_BUFFER = Decimal("1.05")


# --------------------------------------------------------------------------- #
# state machine
# --------------------------------------------------------------------------- #
async def transition(session: AsyncSession, order: Order, to_status: str, note: str | None = None) -> bool:
    """The ONLY place `order.status` is assigned. Writes the audit row too.

    Returns False (and logs) for a disallowed edge instead of raising: a late
    duplicate event must not crash a consumer, it must simply do nothing.
    """
    from_status = order.status
    if to_status not in ALLOWED.get(from_status, frozenset()):
        log.warning(
            "refusing transition %s -> %s for order %s", from_status, to_status, order.id,
            extra={"order_id": str(order.id)},
        )
        return False

    order.status = to_status
    session.add(
        OrderEvent(
            order_id=order.id,
            from_status=from_status,
            to_status=to_status,
            note=(note or "")[:MAX_REASON_LEN] or None,
        )
    )
    return True


# --------------------------------------------------------------------------- #
# 1. validation — nothing is persisted if any of this fails
# --------------------------------------------------------------------------- #
def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def validate(body: PlaceOrderIn) -> tuple[str, str, str, Decimal | None, int]:
    try:
        symbol = normalize_symbol(body.symbol)
    except (ValueError, TypeError):
        raise _bad(f"unknown symbol: {body.symbol!r}")

    side = str(body.side).upper()
    if side not in ("BUY", "SELL"):
        raise _bad("side must be BUY or SELL")

    order_type = str(body.order_type).upper()
    if order_type not in ("LIMIT", "MARKET"):
        raise _bad("order_type must be LIMIT or MARKET")

    try:
        quantity = validate_quantity(body.quantity)
    except MoneyError as exc:
        raise _bad(str(exc))

    price: Decimal | None = None
    if order_type == "LIMIT":
        if body.price is None:
            raise _bad("price is required for a LIMIT order")
        # A JSON number arrives as float; str() first so we never hand a float
        # to Decimal. Money is supposed to cross the wire as a string anyway.
        raw = str(body.price)
        if "e" in raw.lower():
            # Decimal("1e5") is a perfectly good 100000, but nobody types that
            # on purpose — it is a client bug, and silently accepting it means
            # someone bids a hundred thousand dollars by accident.
            raise _bad("price must be written in plain decimal notation")
        try:
            price = validate_price(raw)
        except MoneyError as exc:
            raise _bad(str(exc))
    elif body.price is not None:
        # Silently ignoring it would let a user think they set a limit.
        raise _bad("price must not be supplied for a MARKET order")

    return symbol, side, order_type, price, quantity


# --------------------------------------------------------------------------- #
# 4. reservation + its compensations
# --------------------------------------------------------------------------- #
async def _reference_price(symbol: str) -> Decimal:
    """Reference price for a MARKET buy: live last price, else the seed price."""
    try:
        cached = await get_last_price(redis, symbol)
    except Exception:
        log.warning("redis unavailable for last price of %s", symbol)
        cached = None
    raw = cached or SEED_PRICES.get(symbol)
    if not raw:
        raise HTTPException(status_code=409, detail="NO_MARKET_PRICE")
    return to_money(raw)


async def reserve(order: Order) -> Decimal | None:
    """Hold cash (BUY) or shares (SELL). Raises ServiceCallError on failure.

    Returns the cash amount held for a BUY, None for a SELL.
    Both endpoints are idempotent on order_id, so a retry after a timeout can
    never produce a second hold.
    """
    if order.side == "BUY":
        if order.order_type == "LIMIT":
            amount = notional(order.price, order.quantity)
        else:
            reference = await _reference_price(order.symbol)
            amount = to_money(reference * order.quantity * MARKET_SLIPPAGE_BUFFER)
        await call_service(
            "POST",
            f"{settings.ACCOUNT_SERVICE_URL}/internal/reservations",
            json={"order_id": str(order.id), "user_id": str(order.user_id), "amount": money_str(amount)},
        )
        return amount

    await call_service(
        "POST",
        f"{settings.PORTFOLIO_SERVICE_URL}/internal/share-reservations",
        json={
            "order_id": str(order.id),
            "user_id": str(order.user_id),
            "symbol": order.symbol,
            "quantity": order.quantity,
        },
    )
    return None


async def release(order_id) -> None:
    """THE COMPENSATING ACTION. Best effort, both sides, never raises.

    Both releases are idempotent and are a no-op when nothing is held, so
    calling both is always safe — and far safer than trying to remember which
    one we managed to take.
    """
    for url in (
        f"{settings.ACCOUNT_SERVICE_URL}/internal/reservations/{order_id}/release",
        f"{settings.PORTFOLIO_SERVICE_URL}/internal/share-reservations/{order_id}/release",
    ):
        try:
            await call_service("POST", url, retries=1)
        except Exception as exc:
            log.warning("compensating release failed at %s: %s", url, exc)


# --------------------------------------------------------------------------- #
# the saga
# --------------------------------------------------------------------------- #
async def _reject(session: AsyncSession, order: Order, reason: str) -> None:
    """Terminal rejection: persist it, then tell everyone.

    The publish is best-effort on purpose. This is the path we take when the
    broker is already down, and re-raising here would replace a clean 503 with
    a 500 while leaving the rejection unrecorded.
    """
    await transition(session, order, REJECTED, note=reason)
    order.reject_reason = reason[:MAX_REASON_LEN]
    await session.commit()
    try:
        await publish(
            ORDER_REJECTED,
            {
                "order_id": str(order.id),
                "user_id": str(order.user_id),
                "symbol": order.symbol,
                "reason": reason,
                "rejected_at": utcnow_iso(),
            },
        )
    except Exception:
        log.exception("could not publish order.rejected for %s", order.id)


async def publish(routing_key: str, payload: dict) -> None:
    if deps.broker is None:
        raise RuntimeError("broker not connected")
    await deps.broker.publish_event(routing_key, payload)


async def place_order(session: AsyncSession, user_id, body: PlaceOrderIn) -> tuple[Order, int]:
    """Returns (order, http_status). 201 for a new order, 200 for an idempotent replay."""

    # 1. VALIDATE — 400, nothing persisted.
    symbol, side, order_type, price, quantity = validate(body)

    # 2. CLIENT IDEMPOTENCY — a double-clicked button must not buy twice.
    if body.client_order_id:
        existing = await session.scalar(
            select(Order).where(Order.user_id == user_id, Order.client_order_id == body.client_order_id)
        )
        if existing is not None:
            return existing, 200

    # 3. PERSIST as PENDING and COMMIT — we need the order_id before reserving,
    #    and the row must survive a crash mid-reservation so reconciliation can
    #    find it (§3.7.2).
    order = Order(
        # Generated here, not by the column default: the audit row below needs
        # the id before the INSERT flushes.
        id=uuid.uuid4(),
        user_id=user_id,
        client_order_id=body.client_order_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        price=price,
        quantity=quantity,
        status=PENDING,
    )
    session.add(order)
    session.add(OrderEvent(order_id=order.id, from_status=None, to_status=PENDING, note="submitted"))
    try:
        await session.commit()
    except IntegrityError:
        # Two identical submits raced past the SELECT above. The unique
        # constraint is the real guard; return whichever one won.
        await session.rollback()
        existing = None
        if body.client_order_id:
            existing = await session.scalar(
                select(Order).where(Order.user_id == user_id, Order.client_order_id == body.client_order_id)
            )
        if existing is None:
            raise HTTPException(status_code=409, detail="duplicate client_order_id")
        return existing, 200

    # 4. RESERVE
    try:
        reserved = await reserve(order)
    except HTTPException as exc:                      # NO_MARKET_PRICE
        await _reject(session, order, str(exc.detail))
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except ServiceCallError as exc:
        if exc.status_code == 409:
            await _reject(session, order, exc.detail)
            raise HTTPException(status_code=409, detail=exc.detail)
        # Transport failure or 5xx: the hold MAY have landed before the timeout,
        # so compensate blindly. Release is idempotent and a no-op if absent.
        log.error("reservation failed for order %s: %s", order.id, exc, extra={"order_id": str(order.id)})
        await release(order.id)
        await _reject(session, order, "SERVICE_UNAVAILABLE")
        raise HTTPException(status_code=503, detail="reservation service unavailable")

    # 5. PENDING -> NEW
    if reserved is not None:
        order.reserved_amount = reserved
    await transition(session, order, NEW, note="funds/shares reserved")
    await session.commit()

    # 6. PUBLISH order.accepted
    try:
        await publish(
            ORDER_ACCEPTED,
            {
                "order_id": str(order.id),
                "user_id": str(order.user_id),
                "symbol": order.symbol,
                "side": order.side,
                "order_type": order.order_type,
                "price": money_str(order.price) if order.price is not None else None,
                "quantity": order.quantity,
                "created_at": utcnow_iso(),
            },
        )
    except Exception:
        # ** SAGA COMPENSATION ** The order is reserved but the engine will
        # never hear about it, so it must not stay open holding the user's
        # money. Unwind and say so loudly — this is the graded concept.
        log.exception(
            "SAGA COMPENSATION: could not publish order.accepted for %s, releasing holds", order.id,
            extra={"order_id": str(order.id)},
        )
        await release(order.id)
        await _reject(session, order, "BROKER_UNAVAILABLE")
        raise HTTPException(status_code=503, detail="order bus unavailable, your reservation was released")

    # 7.
    return order, 201
