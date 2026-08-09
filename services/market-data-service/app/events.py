import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.redis_client import redis, seen_event
from common.money import to_money, money_str
from common.symbols import normalize_symbol
from app.models import Trade, Candle

log = logging.getLogger(__name__)

def floor_bucket(ts: datetime, minutes: int) -> datetime:
    ts = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return ts.replace(minute=(ts.minute // minutes) * minutes)

async def upsert_candle(s, sym: str, interval: str, bucket: datetime, price: float, qty: int):
    stmt = pg_insert(Candle).values(
        id=uuid.uuid4(), symbol=sym, interval=interval, bucket_start=bucket,
        open=price, high=price, low=price, close=price,
        volume=qty, trade_count=1
    )
    await s.execute(stmt.on_conflict_do_update(
        index_elements=["symbol", "interval", "bucket_start"],
        set_={
            "high": func.greatest(Candle.high, stmt.excluded.high),
            "low": func.least(Candle.low, stmt.excluded.low),
            "close": stmt.excluded.close,
            "volume": Candle.volume + stmt.excluded.volume,
            "trade_count": Candle.trade_count + 1
        }
    ))

async def handle_trade(env: dict, WriteSession):
    if await seen_event(redis, "marketdata", env["event_id"]): 
        return

    try:
        p = env["payload"]
        sym = normalize_symbol(p["symbol"])
        price = to_money(p["price"])
        qty = int(p["quantity"])
        ts = datetime.fromisoformat(p["executed_at"].replace("Z", "+00:00"))
        
        async with WriteSession() as s, s.begin():
            # Insert Trade
            await s.execute(
                pg_insert(Trade).values(
                    id=uuid.uuid4(),
                    trade_id=uuid.UUID(env["event_id"]), # Use event_id for idempotency
                    symbol=sym,
                    price=price,
                    quantity=qty,
                    aggressor_side=p.get("aggressor_side"),
                    executed_at=ts
                ).on_conflict_do_nothing(index_elements=["trade_id"])
            )
            
            # Upsert Candles
            for interval, minutes in (("1m", 1), ("5m", 5)):
                await upsert_candle(s, sym, interval, floor_bucket(ts, minutes), price, qty)
        
        # Redis caching
        await redis.set(f"md:last_price:{sym}", money_str(price))
        await redis.set(f"md:quote:{sym}", json.dumps({
            "price": money_str(price), 
            "quantity": qty,
            "ts": ts.isoformat()
        }))
        await redis.publish("md:ticks", json.dumps({
            "symbol": sym, 
            "price": money_str(price),
            "quantity": qty, 
            "ts": int(ts.timestamp())
        }))
    except Exception:
        log.exception("Error handling trade")
        raise
