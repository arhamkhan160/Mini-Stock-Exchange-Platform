"""HTTP surface. Business rules live in `service.py`; this file is transport."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.events import ORDER_CANCEL_REQUESTED, utcnow_iso
from common.money import money_str
from common.security import CurrentUser, get_current_user, require_internal_key
from common.symbols import normalize_symbol

from .deps import get_session
from .models import CANCEL_PENDING, CANCELLABLE, OPEN, TERMINAL, Order, OrderEvent
from .schemas import CancelAccepted, OrderEventOut, OrderOut, PlaceOrderIn
from .service import place_order, publish, transition

log = logging.getLogger(__name__)
router = APIRouter()

MAX_PAGE = 200


async def _owned(session: AsyncSession, order_id: uuid.UUID, user: CurrentUser) -> Order:
    """404 if it does not exist, 403 if it is not yours — in that order."""
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if order.user_id != user.id:
        raise HTTPException(status_code=403, detail="not your order")
    return order


@router.post("/orders", response_model=OrderOut, status_code=201, tags=["orders"])
async def create_order(
    body: PlaceOrderIn,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Place an order. 201 for a new order, 200 when `client_order_id` replays one."""
    order, code = await place_order(session, user.id, body)
    response.status_code = code
    return OrderOut.of(order)


@router.get("/orders", response_model=list[OrderOut], tags=["orders"])
async def list_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Your own orders, newest first. Served by ix_orders_user_created."""
    query = select(Order).where(Order.user_id == user.id)
    if status_filter:
        # Comma-separated so the orders page can ask for its Open bucket in one call.
        wanted = [s.strip().upper() for s in status_filter.split(",") if s.strip()]
        query = query.where(Order.status.in_(wanted))
    if symbol:
        try:
            query = query.where(Order.symbol == normalize_symbol(symbol))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail=f"unknown symbol: {symbol}")

    rows = (await session.scalars(
        query.order_by(Order.created_at.desc()).limit(limit).offset(offset)
    )).all()
    return [OrderOut.of(o) for o in rows]


@router.get("/orders/{order_id}", response_model=OrderOut, tags=["orders"])
async def get_order(
    order_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return OrderOut.of(await _owned(session, order_id, user))


@router.get("/orders/{order_id}/events", response_model=list[OrderEventOut], tags=["orders"])
async def get_order_events(
    order_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """The audit trail: every state change this order ever made."""
    await _owned(session, order_id, user)
    rows = (await session.scalars(
        select(OrderEvent).where(OrderEvent.order_id == order_id).order_by(OrderEvent.created_at)
    )).all()
    return [OrderEventOut.model_validate(r) for r in rows]


@router.delete(
    "/orders/{order_id}",
    response_model=CancelAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["orders"],
)
async def cancel_order(
    order_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Phase one of a two-phase cancel. 202, never 200 — the BOOK decides.

    We ask; the matching engine answers with order.cancelled or
    order.cancel_rejected. Releasing the hold here instead would settle a fill
    landing in the same millisecond against a reservation that no longer exists.
    """
    order = await _owned(session, order_id, user)

    if order.status in TERMINAL:
        raise HTTPException(status_code=409, detail=f"order is already {order.status}")
    if order.status == CANCEL_PENDING:
        raise HTTPException(status_code=409, detail="cancellation already in progress")
    if order.status not in CANCELLABLE:                     # PENDING: still mid-saga
        raise HTTPException(status_code=409, detail="order is still being submitted")

    previous = order.status
    await transition(session, order, CANCEL_PENDING, note="user requested cancellation")
    await session.commit()

    try:
        await publish(ORDER_CANCEL_REQUESTED, {
            "order_id": str(order.id),
            "user_id": str(order.user_id),
            "symbol": order.symbol,
            "side": order.side,
            "requested_at": utcnow_iso(),
        })
    except Exception:
        # Nobody will ever answer, so do not leave the order stuck "Cancelling…".
        log.exception("could not publish order.cancel_requested for %s", order.id)
        await transition(session, order, previous, note="cancel request could not be sent")
        await session.commit()
        raise HTTPException(status_code=503, detail="order bus unavailable, cancellation not sent")

    return CancelAccepted()


@router.get("/internal/orders/open", tags=["internal"], dependencies=[Depends(require_internal_key)])
async def open_orders(session: AsyncSession = Depends(get_session)):
    """Every resting order, for the matching engine's book rebuild on boot.

    Rows are in the order.accepted shape (plus `filled_quantity`) and sorted by
    `created_at`, so the engine can insert them straight into the book and keep
    time priority.
    """
    rows = (await session.scalars(
        select(Order).where(Order.status.in_(OPEN)).order_by(Order.created_at)
    )).all()
    return {
        "orders": [
            {
                "order_id": str(o.id),
                "user_id": str(o.user_id),
                "symbol": o.symbol,
                "side": o.side,
                "order_type": o.order_type,
                "price": money_str(o.price) if o.price is not None else None,
                "quantity": o.quantity,
                "filled_quantity": o.filled_quantity,
                "created_at": o.created_at.isoformat(),
            }
            for o in rows
        ]
    }
