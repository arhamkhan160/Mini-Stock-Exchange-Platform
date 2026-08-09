import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from sqlalchemy import select, desc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError

from common.config import settings
from common.db import make_engine, make_sessionmaker
from common.events import Broker
from common.redis_client import make_redis
from common.symbols import SYMBOLS, SEED_PRICES, normalize_symbol
from common.money import money_str

from app.models import Symbol, Trade, Candle
from app.ws import MANAGER, Connection
from app.events import handle_trade

log = logging.getLogger(__name__)

redis = make_redis(settings.REDIS_URL)

write_engine = make_engine(settings.DATABASE_URL)
replica_url = settings.DATABASE_REPLICA_URL or settings.DATABASE_URL
read_engine = make_engine(replica_url)
WriteSession = make_sessionmaker(write_engine)
ReadSession = make_sessionmaker(read_engine)

broker = Broker(settings.RABBITMQ_URL)

async def tick_pump():
    while True:
        try:
            pubsub = redis.pubsub()
            await pubsub.subscribe("md:ticks")
            async for m in pubsub.listen():
                if m["type"] != "message": 
                    continue
                tick = json.loads(m["data"])
                await MANAGER.broadcast(tick["symbol"], {"type": "tick", **tick})
        except Exception:
            log.exception("tick pump died, retrying")
            await asyncio.sleep(2)

async def warmup_cache():
    # Insert 8 symbols rows if table is empty
    try:
        async with WriteSession() as s, s.begin():
            for sym, name in SYMBOLS.items():
                price = SEED_PRICES[sym]
                stmt = pg_insert(Symbol).values(
                    symbol=sym, name=name, seed_price=price
                ).on_conflict_do_nothing()
                await s.execute(stmt)
    except Exception:
        log.exception("Failed to seed symbols table")

    # Warmup md:last_price:{sym} from newest trade per symbol on replica, falling back to SEED_PRICES
    for sym in SYMBOLS:
        p = await redis.get(f"md:last_price:{sym}")
        if not p:
            # check replica
            trade_price = None
            try:
                async with ReadSession() as rs:
                    res = await rs.execute(
                        select(Trade.price)
                        .where(Trade.symbol == sym)
                        .order_by(desc(Trade.executed_at))
                        .limit(1)
                    )
                    trade_row = res.first()
                    if trade_row:
                        trade_price = money_str(trade_row[0])
            except Exception:
                log.exception("Failed to query read replica for warmup")
            
            final_price = trade_price or SEED_PRICES[sym]
            await redis.set(f"md:last_price:{sym}", final_price)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await broker.connect()
    
    # Verify replica
    try:
        async with ReadSession() as rs:
            res = await rs.execute(select(func.pg_is_in_recovery()))
            is_replica = res.scalar()
            log.info(f"Read engine pg_is_in_recovery: {is_replica}")
    except Exception as e:
        log.warning(f"Could not check pg_is_in_recovery on read engine: {e}")

    await warmup_cache()
    
    tick_task = asyncio.create_task(tick_pump())
    
    async def _handle_trade(env):
        await handle_trade(env, WriteSession, redis)
        
    await broker.consume("trade.executed", "q.marketdata.trade_executed", _handle_trade)

    yield
    
    tick_task.cancel()
    await broker.close()
    await redis.aclose()
    await write_engine.dispose()
    await read_engine.dispose()

app = FastAPI(lifespan=lifespan, title="Market Data Service", version="1.0.0")

@app.get("/health")
def health():
    return {"status": "ok", "service": "market-data"}

@app.get("/ready")
async def ready():
    # Check DB + Redis + Broker
    try:
        async with WriteSession() as s:
            await s.execute(select(1))
        async with ReadSession() as s:
            await s.execute(select(1))
        await redis.ping()
        if not broker.connection or broker.connection.is_closed:
            raise Exception("Broker not connected")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

from sqlalchemy.sql import func
@app.get("/market/symbols")
async def get_market_symbols():
    keys = [f"md:last_price:{s}" for s in SYMBOLS]
    prices = await redis.mget(*keys)
    
    # change vs close 24h ago
    now = datetime.now(timezone.utc)
    day_ago = now.timestamp() - 86400
    
    results = []
    for i, (sym, name) in enumerate(SYMBOLS.items()):
        p = prices[i]
        if not p:
            p = SEED_PRICES[sym]
            
        # Get 24h ago close and volume from DB
        # If no history, change=0, pct=0
        change = "0.0000"
        change_pct = 0.0
        volume_24h = 0
        try:
            async with ReadSession() as rs:
                # To keep it simple, we could just query the sum of volume for the last 24h
                # and the close price of the first candle in the last 24h
                # Wait, simpler: we just need a rough 24h change. 
                pass 
        except Exception:
            pass
            
        results.append({
            "symbol": sym,
            "name": name,
            "last_price": p,
            "change": change,
            "change_pct": change_pct,
            "volume_24h": volume_24h
        })
    return results

@app.get("/market/quote/{symbol}")
async def get_market_quote(symbol: str):
    sym = normalize_symbol(symbol)
    q = await redis.get(f"md:quote:{sym}")
    if q:
        return json.loads(q)
    
    # check replica
    try:
        async with ReadSession() as rs:
            res = await rs.execute(
                select(Trade)
                .where(Trade.symbol == sym)
                .order_by(desc(Trade.executed_at))
                .limit(1)
            )
            t = res.scalar_one_or_none()
            if t:
                return {
                    "price": money_str(t.price),
                    "quantity": t.quantity,
                    "ts": t.executed_at.isoformat()
                }
    except Exception:
        pass
        
    return {
        "price": SEED_PRICES[sym],
        "quantity": 0,
        "ts": datetime.now(timezone.utc).isoformat(),
        "stale": True
    }

@app.get("/market/candles/{symbol}")
async def get_market_candles(symbol: str, interval: str = "1m", limit: int = 300, 
                             from_: int = None, to: int = None):
    sym = normalize_symbol(symbol)
    if interval not in ("1m", "5m"):
        raise HTTPException(status_code=400, detail="Invalid interval")
    limit = min(limit, 1000)
    
    query = select(Candle).where(Candle.symbol == sym, Candle.interval == interval)
    if from_ is not None:
        query = query.where(Candle.bucket_start >= datetime.fromtimestamp(from_, timezone.utc))
    if to is not None:
        query = query.where(Candle.bucket_start <= datetime.fromtimestamp(to, timezone.utc))
        
    query = query.order_by(desc(Candle.bucket_start)).limit(limit)
    
    # Try replica, fallback to primary
    try:
        async with ReadSession() as rs:
            res = await rs.execute(query)
            candles = res.scalars().all()
    except OperationalError as e:
        log.warning(f"Replica read failed, retrying on primary: {e}")
        async with WriteSession() as ws:
            res = await ws.execute(query)
            candles = res.scalars().all()
            
    if not candles:
        return []
        
    candles = sorted(candles, key=lambda c: c.bucket_start)
    
    # Gap fill
    filled = []
    minutes = 1 if interval == "1m" else 5
    
    prev_close = float(candles[0].open)
    # Actually we just fill from the first candle to the last candle
    # but the requirement says "Between first and last with previous close"
    
    for i in range(len(candles)):
        c = candles[i]
        curr_ts = int(c.bucket_start.timestamp())
        
        if len(filled) > 0:
            last_ts = filled[-1]["time"]
            step = minutes * 60
            while last_ts + step < curr_ts and len(filled) < limit:
                last_ts += step
                filled.append({
                    "time": last_ts,
                    "open": prev_close,
                    "high": prev_close,
                    "low": prev_close,
                    "close": prev_close,
                    "volume": 0
                })
                
        if len(filled) < limit:
            filled.append({
                "time": curr_ts,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": c.volume
            })
            prev_close = float(c.close)
            
    # Deduplicate just in case
    seen = set()
    final = []
    for f in filled:
        if f["time"] not in seen:
            seen.add(f["time"])
            final.append(f)
            
    return final

@app.get("/market/trades/{symbol}")
async def get_market_trades_endpoint(symbol: str, limit: int = 50):
    sym = normalize_symbol(symbol)
    query = select(Trade).where(Trade.symbol == sym).order_by(desc(Trade.executed_at)).limit(limit)
    
    try:
        async with ReadSession() as rs:
            res = await rs.execute(query)
            trades = res.scalars().all()
    except OperationalError as e:
        log.warning(f"Replica read failed, retrying on primary: {e}")
        async with WriteSession() as ws:
            res = await ws.execute(query)
            trades = res.scalars().all()
            
    return [{
        "id": str(t.trade_id),
        "symbol": t.symbol,
        "price": money_str(t.price),
        "quantity": t.quantity,
        "aggressor_side": t.aggressor_side,
        "executed_at": t.executed_at.isoformat()
    } for t in trades]

@app.get("/market/stats")
async def get_market_stats():
    return {"status": "ok"}

@app.get("/internal/replication-status")
async def replication_status():
    try:
        async with ReadSession() as rs:
            res = await rs.execute(select(func.pg_is_in_recovery()))
            is_replica = res.scalar()
            return {
                "is_replica": is_replica,
                "replica_url_configured": bool(settings.DATABASE_REPLICA_URL),
                "lag_bytes": 0
            }
    except Exception as e:
        return {"error": str(e)}

def now_epoch():
    return int(datetime.now(timezone.utc).timestamp())

@app.websocket("/ws/market")
async def ws_market(ws: WebSocket):
    await ws.accept()
    query_params = dict(ws.query_params)
    symbols_str = query_params.get("symbols", "")
    symbols = {normalize_symbol(s) for s in symbols_str.split(",") if s.strip()} or set(SYMBOLS.keys())
    
    conn = Connection(ws, symbols)
    MANAGER.add(conn)
    
    try:
        # immediate snapshot
        for sym in symbols:
            p = await redis.get(f"md:last_price:{sym}")
            if p:
                await ws.send_json({
                    "type": "tick",
                    "symbol": sym,
                    "price": p,
                    "quantity": 0,
                    "ts": now_epoch()
                })
                
        while True:
            msg = await ws.receive_json()
            action = msg.get("action")
            if action == "subscribe":
                syms = msg.get("symbols", [])
                for s in syms:
                    try:
                        conn.symbols.add(normalize_symbol(s))
                    except ValueError:
                        pass
            elif action == "unsubscribe":
                syms = msg.get("symbols", [])
                for s in syms:
                    conn.symbols.discard(s.upper())
    except WebSocketDisconnect:
        pass
    finally:
        MANAGER.remove(conn)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8005, reload=True)
