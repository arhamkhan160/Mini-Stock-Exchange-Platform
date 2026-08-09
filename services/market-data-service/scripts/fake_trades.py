import asyncio
import json
import random
import sys
import os
import time
import uuid
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "libs")))

from common.config import settings
from common.events import Broker
from common.symbols import SYMBOLS, SEED_PRICES

async def main():
    broker = Broker(settings.RABBITMQ_URL)
    await broker.connect()
    
    print("Starting fake trades...")
    try:
        while True:
            sym = random.choice(list(SYMBOLS.keys()))
            price = float(SEED_PRICES[sym]) + random.uniform(-1, 1)
            qty = random.randint(1, 100)
            
            payload = {
                "trade_id": str(uuid.uuid4()),
                "symbol": sym,
                "price": f"{price:.4f}",
                "quantity": qty,
                "buy_order_id": str(uuid.uuid4()),
                "sell_order_id": str(uuid.uuid4()),
                "buyer_user_id": str(uuid.uuid4()),
                "seller_user_id": str(uuid.uuid4()),
                "aggressor_side": random.choice(["BUY", "SELL"]),
                "buy_order_remaining": 0,
                "sell_order_remaining": 0,
                "buy_order_limit_price": None,
                "executed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            }
            
            await broker.publish("trade.executed", payload)
            await asyncio.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        await broker.close()
        
if __name__ == "__main__":
    asyncio.run(main())
