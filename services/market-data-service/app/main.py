import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from sqlalchemy import select, desc, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError

from common.config import settings
from common.db import make_engine, make_sessionmaker
from common.events import (
    Q_MARKETDATA_TRADE_EXECUTED,
    TRADE_EXECUTED,
    Broker,
)
from .redis_conn import redis
from common.symbols import SYMBOLS, SEED_PRICES, normalize_symbol
from common.money import money_str, to_money

from app.models import Symbol, Trade, Candle
from app.ws import MANAGER, Connection
from app.events import handle_trade

log = logging.getLogger(__name__)

write_engine = make_engine(settings.DATABASE_URL)
replica_url = settings.DATABASE_REPLICA_URL or settings.DATABASE_URL
read_engine = make_engine(replica_url)
WriteSession = make_sessionmaker(write_engine)
ReadSession = make_sessionmaker(read_engine)

broker = Broker(settings.RABBITMQ_URL, settings.SERVICE_NAME)

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
        await handle_trade(env, WriteSession)
        
    await broker.consume(Q_MARKETDATA_TRADE_EXECUTED, [TRADE_EXECUTED], _handle_trade)

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
async def _daily_stats() -> dict[str, dict]:
    """Opening price and traded volume for every symbol over the last 24h.

    Two grouped queries for the whole board rather than two per symbol, and
    served from the read replica like every other history query.
    """
    opens_sql = text(
        """
        SELECT DISTINCT ON (symbol) symbol, open
        FROM candles
        WHERE interval = '1m' AND bucket_start >= now() - interval '24 hours'
        ORDER BY symbol, bucket_start ASC
        """
    )
    volume_sql = text(
        """
        SELECT symbol, COALESCE(SUM(volume), 0) AS volume
        FROM candles
        WHERE interval = '1m' AND bucket_start >= now() - interval '24 hours'
        GROUP BY symbol
        """
    )

    async def fetch(session):
        opens = {row.symbol: row.open for row in (await session.execute(opens_sql)).all()}
        volumes = {row.symbol: int(row.volume) for row in (await session.execute(volume_sql)).all()}
        return opens, volumes

    try:
        async with ReadSession() as rs:
            opens, volumes = await fetch(rs)
    except OperationalError as exc:
        log.warning(f"Replica read failed for daily stats, retrying on primary: {exc}")
        async with WriteSession() as ws:
            opens, volumes = await fetch(ws)

    return {
        sym: {"open": opens.get(sym), "volume": volumes.get(sym, 0)}
        for sym in SYMBOLS
    }


@app.get("/market/symbols")
async def get_market_symbols():
    keys = [f"md:last_price:{s}" for s in SYMBOLS]
    prices = await redis.mget(*keys)

    try:
        stats = await _daily_stats()
    except Exception:
        # The board must still render if the history query fails.
        log.exception("could not compute 24h stats")
        stats = {}

    results = []
    for i, (sym, name) in enumerate(SYMBOLS.items()):
        last_raw = prices[i] or SEED_PRICES[sym]
        last = to_money(last_raw)

        entry = stats.get(sym) or {}
        opened = entry.get("open")
        if opened is not None and to_money(opened) > 0:
            opened = to_money(opened)
            change = to_money(last - opened)
            change_pct = float(change / opened * 100)
        else:
            # No history yet — report a flat market rather than null or a
            # divide-by-zero.
            change = to_money(0)
            change_pct = 0.0

        results.append({
            "symbol": sym,
            "name": name,
            "last_price": money_str(last),
            "change": money_str(change),
            "change_pct": round(change_pct, 2),
            "volume_24h": entry.get("volume", 0),
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

    # ---- gap fill ---------------------------------------------------------
    # A minute with no trades has no row, and a chart with holes reads as
    # broken, so quiet buckets are carried forward flat at the previous close.
    step = (1 if interval == "1m" else 5) * 60
    # A long quiet stretch must not expand into millions of synthetic points.
    MAX_FILL_PER_GAP = 1000

    filled: list[dict] = []
    prev_close = float(candles[0].open)

    for c in candles:
        curr_ts = int(c.bucket_start.timestamp())

        if filled:
            last_ts = filled[-1]["time"]
            produced = 0
            while last_ts + step < curr_ts and produced < MAX_FILL_PER_GAP:
                last_ts += step
                produced += 1
                filled.append({
                    "time": last_ts,
                    "open": prev_close,
                    "high": prev_close,
                    "low": prev_close,
                    "close": prev_close,
                    "volume": 0,
                })

        filled.append({
            "time": curr_ts,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": c.volume,
        })
        prev_close = float(c.close)

    # Trim from the OLD end. Capping while building dropped the newest candles,
    # which are the ones a live chart actually needs — the price would freeze a
    # bucket behind and never show the latest trade.
    filled = filled[-limit:]

    # Deduplicate, keeping order: lightweight-charts asserts on repeated or
    # out-of-order timestamps.
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
