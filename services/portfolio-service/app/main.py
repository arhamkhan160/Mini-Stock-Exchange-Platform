import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.exc import IntegrityError

from common.config import settings
from common.db import make_engine, make_sessionmaker, session_dependency
from common.events import Broker
from common.redis_client import make_redis, distributed_lock
from common.security import get_current_user, CurrentUser, require_internal_key
from common.symbols import SYMBOLS, normalize_symbol
from common.money import money_str, to_money

from app.models import Holding, ShareReservation, TradeHistory
from app.events import handle_trade_executed, handle_order_cancelled_or_rejected

log = logging.getLogger(__name__)

redis = make_redis(settings.REDIS_URL)

engine = make_engine(settings.DATABASE_URL)
Session = make_sessionmaker(engine)
get_session = session_dependency(Session)

broker = Broker(settings.RABBITMQ_URL)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await broker.connect()
    
    async def _trade_executed(env):
        await handle_trade_executed(env, Session, redis)
        
    async def _order_cancelled(env):
        await handle_order_cancelled_or_rejected(env, Session, redis)
        
    await broker.consume("trade.executed", "q.portfolio.trade_executed", _trade_executed)
    await broker.consume("order.cancelled", "q.portfolio.order_cancelled", _order_cancelled)
    await broker.consume("order.rejected", "q.portfolio.order_rejected", _order_cancelled)

    yield
    
    await broker.close()
    await redis.aclose()
    await engine.dispose()

app = FastAPI(lifespan=lifespan, title="Portfolio Service", version="1.0.0")

@app.get("/health")
def health():
    return {"status": "ok", "service": "portfolio"}

@app.get("/ready")
async def ready():
    try:
        async with Session() as s:
            await s.execute(select(1))
        await redis.ping()
        if not broker.connection or broker.connection.is_closed:
            raise Exception("Broker not connected")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

async def _get_portfolio(user_id: uuid.UUID, session):
    holdings = await session.scalars(select(Holding).where(Holding.user_id == user_id))
    holdings = list(holdings)
    
    symbols_to_fetch = [h.symbol for h in holdings]
    if symbols_to_fetch:
        keys = [f"md:last_price:{s}" for s in symbols_to_fetch]
        prices = await redis.mget(*keys)
    else:
        prices = []
        
    price_map = dict(zip(symbols_to_fetch, prices))
    
    result_holdings = []
    total_market_value = Decimal(0)
    total_cost_basis = Decimal(0)
    total_unrealized = Decimal(0)
    total_realized = Decimal(0)
    
    for h in holdings:
        mark_str = price_map.get(h.symbol)
        price_stale = False
        if not mark_str:
            mark = to_money(h.avg_cost)
            price_stale = True
        else:
            mark = to_money(mark_str)
            
        market_val = to_money(mark * h.quantity)
        cost_basis = to_money(to_money(h.avg_cost) * h.quantity)
        unrealized = to_money(market_val - cost_basis)
        realized = to_money(h.realized_pnl)
        
        total_market_value += market_val
        total_cost_basis += cost_basis
        total_unrealized += unrealized
        total_realized += realized
        
        result_holdings.append({
            "symbol": h.symbol,
            "name": SYMBOLS.get(h.symbol, h.symbol),
            "quantity": h.quantity,
            "available_quantity": h.quantity - h.reserved_quantity,
            "reserved_quantity": h.reserved_quantity,
            "avg_cost": money_str(h.avg_cost),
            "last_price": money_str(mark),
            "market_value": money_str(market_val),
            "unrealized_pnl": money_str(unrealized),
            "realized_pnl": money_str(realized),
            "price_stale": price_stale
        })
        
    return {
        "user_id": str(user_id),
        "holdings": result_holdings,
        "totals": {
            "total_market_value": money_str(total_market_value),
            "total_cost_basis": money_str(total_cost_basis),
            "total_unrealized_pnl": money_str(total_unrealized),
            "total_realized_pnl": money_str(total_realized),
            "as_of": datetime.now(timezone.utc).isoformat()
        }
    }

@app.get("/portfolio")
async def get_portfolio(user: CurrentUser = Depends(get_current_user), session = Depends(get_session)):
    return await _get_portfolio(user.id, session)

@app.get("/portfolio/pnl")
async def get_pnl(user: CurrentUser = Depends(get_current_user), session = Depends(get_session)):
    pf = await _get_portfolio(user.id, session)
    return pf["totals"]

@app.get("/portfolio/trades")
async def get_trades(
    symbol: str = "", limit: int = 50, offset: int = 0, 
    user: CurrentUser = Depends(get_current_user), session = Depends(get_session)
):
    query = select(TradeHistory).where(TradeHistory.user_id == user.id)
    if symbol:
        sym = normalize_symbol(symbol)
        query = query.where(TradeHistory.symbol == sym)
    query = query.order_by(desc(TradeHistory.executed_at)).offset(offset).limit(limit)
    
    trades = await session.scalars(query)
    return [{
        "id": str(t.trade_id),
        "symbol": t.symbol,
        "side": t.side,
        "quantity": t.quantity,
        "price": money_str(t.price),
        "realized_pnl": money_str(t.realized_pnl),
        "executed_at": t.executed_at.isoformat()
    } for t in trades]

@app.get("/portfolio/holdings/{symbol}")
async def get_holding(symbol: str, user: CurrentUser = Depends(get_current_user), session = Depends(get_session)):
    sym = normalize_symbol(symbol)
    holding = await session.scalar(select(Holding).where(Holding.user_id == user.id, Holding.symbol == sym))
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")
        
    pf = await _get_portfolio(user.id, session)
    for h in pf["holdings"]:
        if h["symbol"] == sym:
            return h
    raise HTTPException(status_code=404, detail="Holding not found")


class ShareResReq(BaseModel):
    order_id: uuid.UUID
    user_id: uuid.UUID
    symbol: str
    quantity: int

@app.post("/internal/share-reservations")
async def reserve_shares(req: ShareResReq, session = Depends(get_session), _ = Depends(require_internal_key)):
    sym = normalize_symbol(req.symbol)
    
    # Idempotent check
    res = await session.get(ShareReservation, req.order_id)
    if res:
        return {"reservation_id": str(res.order_id), "order_id": str(res.order_id), "quantity": res.quantity, "status": res.status}
        
    async with distributed_lock(redis, f"lock:shares:{req.user_id}:{sym}"):
        holding = await session.scalar(select(Holding).where(Holding.user_id == req.user_id, Holding.symbol == sym))
        if not holding:
            raise HTTPException(status_code=409, detail="insufficient shares")
            
        available = holding.quantity - holding.reserved_quantity
        if available < req.quantity:
            raise HTTPException(status_code=409, detail="insufficient shares")
            
        holding.reserved_quantity += req.quantity
        
        res = ShareReservation(
            order_id=req.order_id, user_id=req.user_id, symbol=sym, 
            quantity=req.quantity, consumed=0, released=0, status="HELD"
        )
        session.add(res)
        await session.commit()
        
    return {"reservation_id": str(res.order_id), "order_id": str(res.order_id), "quantity": res.quantity, "status": res.status}

@app.post("/internal/share-reservations/{order_id}/release")
async def release_shares(order_id: uuid.UUID, session = Depends(get_session), _ = Depends(require_internal_key)):
    res = await session.get(ShareReservation, order_id)
    if not res or res.status == "RELEASED":
        return {"released": 0}
        
    async with distributed_lock(redis, f"lock:shares:{res.user_id}:{res.symbol}"):
        # Refetch to ensure latest state inside lock
        res = await session.get(ShareReservation, order_id)
        if not res or res.status == "RELEASED":
            return {"released": 0}
            
        release_qty = res.quantity - res.consumed - res.released
        res.released += release_qty
        res.status = "RELEASED"
        
        holding = await session.scalar(select(Holding).where(Holding.user_id == res.user_id, Holding.symbol == res.symbol))
        if holding:
            holding.reserved_quantity -= release_qty
            if holding.reserved_quantity < 0:
                holding.reserved_quantity = 0
                
        await session.commit()
        return {"released": release_qty}

@app.get("/internal/holdings/{user_id}")
async def get_internal_holdings(user_id: uuid.UUID, session = Depends(get_session), _ = Depends(require_internal_key)):
    holdings = await session.scalars(select(Holding).where(Holding.user_id == user_id))
    return [{"symbol": h.symbol, "quantity": h.quantity, "reserved": h.reserved_quantity} for h in holdings]
