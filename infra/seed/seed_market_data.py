import asyncio
import os
import sys
import random
import uuid
from datetime import datetime, timezone, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "libs")))

from common.symbols import SYMBOLS, SEED_PRICES
from common.redis_client import make_redis

import asyncpg

# Environment variables matching the docker-compose setup
# For direct seed, we expect this script to be run either via make seed 
# where env vars are set, or we default to local dev values.
DB_USER = os.getenv("POSTGRES_USER", "mse")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "mse_pw")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5436")
DB_NAME = os.getenv("POSTGRES_DB", "market_db")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

async def seed_market_data():
    print(f"Connecting to Postgres at {DB_HOST}:{DB_PORT}...")
    try:
        conn = await asyncpg.connect(user=DB_USER, password=DB_PASS, host=DB_HOST, port=DB_PORT, database=DB_NAME)
    except Exception as e:
        print(f"Failed to connect to database: {e}")
        return

    # Check idempotency
    count = await conn.fetchval("SELECT count(*) FROM candles")
    if count > 0:
        print("Candles table already populated. Skipping seed.")
        await conn.close()
        return

    print("Inserting symbols...")
    for sym, name in SYMBOLS.items():
        price = SEED_PRICES[sym]
        await conn.execute(
            """
            INSERT INTO symbols (symbol, name, seed_price)
            VALUES ($1, $2, $3)
            ON CONFLICT (symbol) DO NOTHING
            """,
            sym, name, float(price)
        )

    print("Generating 2 days of historical candles...")
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start_time = now - timedelta(days=2)
    
    random.seed(42)  # reproducible

    redis = make_redis(REDIS_URL)

    for sym in SYMBOLS.keys():
        print(f"Generating for {sym}...")
        current_time = start_time
        price = float(SEED_PRICES[sym])
        
        candles_1m = []
        
        while current_time < now:
            # Random walk
            change = price * random.gauss(0, 0.0006)
            close = price + change
            high = max(price, close) + abs(price * random.gauss(0, 0.0002))
            low = min(price, close) - abs(price * random.gauss(0, 0.0002))
            volume = max(0, int(random.gauss(100, 50)))
            
            candles_1m.append({
                'id': str(uuid.uuid4()),
                'symbol': sym,
                'interval': '1m',
                'bucket_start': current_time,
                'open': price,
                'high': high,
                'low': low,
                'close': close,
                'volume': volume,
                'trade_count': max(1, volume // 10)
            })
            
            price = close
            current_time += timedelta(minutes=1)

        # Insert 1m candles
        print(f"  Inserting {len(candles_1m)} 1m candles for {sym}...")
        await conn.executemany(
            """
            INSERT INTO candles (id, symbol, interval, bucket_start, open, high, low, close, volume, trade_count)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            [(c['id'], c['symbol'], c['interval'], c['bucket_start'], c['open'], c['high'], c['low'], c['close'], c['volume'], c['trade_count']) for c in candles_1m]
        )

        # Generate and insert 5m candles
        candles_5m = []
        for i in range(0, len(candles_1m), 5):
            chunk = candles_1m[i:i+5]
            if not chunk:
                continue
            c5 = {
                'id': str(uuid.uuid4()),
                'symbol': sym,
                'interval': '5m',
                'bucket_start': chunk[0]['bucket_start'],
                'open': chunk[0]['open'],
                'high': max(c['high'] for c in chunk),
                'low': min(c['low'] for c in chunk),
                'close': chunk[-1]['close'],
                'volume': sum(c['volume'] for c in chunk),
                'trade_count': sum(c['trade_count'] for c in chunk)
            }
            candles_5m.append(c5)

        print(f"  Inserting {len(candles_5m)} 5m candles for {sym}...")
        await conn.executemany(
            """
            INSERT INTO candles (id, symbol, interval, bucket_start, open, high, low, close, volume, trade_count)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            [(c['id'], c['symbol'], c['interval'], c['bucket_start'], c['open'], c['high'], c['low'], c['close'], c['volume'], c['trade_count']) for c in candles_5m]
        )

        # Set md:last_price in Redis
        last_close = f"{price:.4f}"
        print(f"  Setting md:last_price:{sym} = {last_close}")
        await redis.set(f"md:last_price:{sym}", last_close)
        
    await conn.close()
    await redis.aclose()
    print("Seed complete.")

if __name__ == "__main__":
    asyncio.run(seed_market_data())
