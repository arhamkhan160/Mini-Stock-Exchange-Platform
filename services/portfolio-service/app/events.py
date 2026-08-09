import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from common.money import to_money
from common.redis_client import redis, distributed_lock
from common.symbols import normalize_symbol
from app.models import Holding, ProcessedEvent, ShareReservation, TradeHistory

log = logging.getLogger(__name__)


async def seen_event(redis_client, service: str, event_id: str) -> bool:
    """Read-only check so we don't SET NX before DB commit."""
    if not event_id:
        return False
    val = await redis_client.get(f"idem:{service}:{event_id}")
    return bool(val)


async def mark_event_processed(redis_client, service: str, event_id: str) -> None:
    """Written AFTER the DB commit to act as a fast cache for the PK."""
    if event_id:
        await redis_client.set(f"idem:{service}:{event_id}", "1", ex=86400)


async def get_or_create_holding(session, user_id: uuid.UUID, symbol: str) -> Holding:
    holding = await session.scalar(
        select(Holding).where(Holding.user_id == user_id, Holding.symbol == symbol)
    )
    if not holding:
        holding = Holding(
            user_id=user_id,
            symbol=symbol,
            quantity=0,
            reserved_quantity=0,
            avg_cost=0,
            realized_pnl=0,
        )
        session.add(holding)
    return holding


async def handle_trade_executed(env: dict, Session):
    event_id = env["event_id"]
    if await seen_event(redis, "portfolio", event_id):
        return

    p = env["payload"]
    symbol = normalize_symbol(p["symbol"])
    qty = int(p["quantity"])
    price = to_money(p["price"])
    ts = datetime.fromisoformat(p["executed_at"].replace("Z", "+00:00"))
    
    buyer_id = uuid.UUID(p["buyer_user_id"])
    seller_id = uuid.UUID(p["seller_user_id"])
    trade_uuid = uuid.UUID(p["trade_id"])
    
    sell_order_id = uuid.UUID(p["sell_order_id"])
    sell_remaining = int(p["sell_order_remaining"])

    # Handle self-trade
    users = [buyer_id, seller_id]
    if buyer_id == seller_id:
        users = [buyer_id]

    # Acquire locks sequentially, sorting to prevent deadlocks if cross-trades happen
    users.sort()
    
    async def process(session):
        # BUY logic
        b_holding = await get_or_create_holding(session, buyer_id, symbol)
        new_qty = b_holding.quantity + qty
        if new_qty > 0:
            new_avg = to_money((b_holding.quantity * to_money(b_holding.avg_cost) + qty * price) / new_qty)
            b_holding.avg_cost = new_avg
        b_holding.quantity = new_qty
        
        session.add(TradeHistory(
            trade_id=trade_uuid, user_id=buyer_id, symbol=symbol, side="BUY",
            quantity=qty, price=price, realized_pnl=0, executed_at=ts
        ))

        # SELL logic
        s_holding = await get_or_create_holding(session, seller_id, symbol)
        realized = to_money((price - to_money(s_holding.avg_cost)) * qty)
        s_holding.realized_pnl = to_money(to_money(s_holding.realized_pnl) + realized)
        s_holding.quantity -= qty
        s_holding.reserved_quantity -= min(s_holding.reserved_quantity, qty)
        if s_holding.quantity == 0:
            s_holding.avg_cost = 0
            
        session.add(TradeHistory(
            trade_id=trade_uuid, user_id=seller_id, symbol=symbol, side="SELL",
            quantity=qty, price=price, realized_pnl=realized, executed_at=ts
        ))

        # Consume reservation
        res = await session.get(ShareReservation, sell_order_id)
        if res:
            res.consumed += min(res.quantity - res.consumed - res.released, qty)
            if sell_remaining == 0:
                res.released += (res.quantity - res.consumed - res.released)
                res.status = "RELEASED"

    # Nested locking wrapper
    async def run_with_locks(idx):
        if idx == len(users):
            async with Session() as session:
                await process(session)
                session.add(ProcessedEvent(event_id=uuid.UUID(event_id), event_type="trade.executed"))
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    return False
            return True
            
        async with distributed_lock(redis, f"lock:shares:{users[idx]}:{symbol}"):
            return await run_with_locks(idx + 1)

    success = await run_with_locks(0)
    if success:
        await mark_event_processed(redis, "portfolio", event_id)


async def handle_order_cancelled_or_rejected(env: dict, Session):
    event_id = env["event_id"]
    if await seen_event(redis, "portfolio", event_id):
        return

    p = env["payload"]
    order_id = uuid.UUID(p["order_id"])

    async with Session() as session:
        res = await session.get(ShareReservation, order_id)
        if res and res.status == "HELD":
            res.released += (res.quantity - res.consumed - res.released)
            res.status = "RELEASED"
            
            # also release from holding
            holding = await get_or_create_holding(session, res.user_id, res.symbol)
            holding.reserved_quantity -= (res.quantity - res.consumed)
            if holding.reserved_quantity < 0:
                holding.reserved_quantity = 0

        session.add(ProcessedEvent(event_id=uuid.UUID(event_id), event_type=env["event_type"]))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return

    await mark_event_processed(redis, "portfolio", event_id)
