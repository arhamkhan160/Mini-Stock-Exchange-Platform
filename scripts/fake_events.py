"""Publish a fake trading event onto the bus — for exercising Account Service
in isolation, without Order/Matching Engine running. Uses the real envelope
helper so the shape always matches the contract in libs/common/events.py.

Usage (run against `docker compose up -d rabbitmq`, i.e. from the host):
    python scripts/fake_events.py trade   <buyer_id> <seller_id> <price> <qty>
    python scripts/fake_events.py cancel  <user_id> <order_id> [qty]
    python scripts/fake_events.py reject  <user_id> <order_id> [reason]
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))

import aio_pika  # noqa: E402

from common.events import EXCHANGE, ORDER_CANCELLED, ORDER_REJECTED, TRADE_EXECUTED, envelope  # noqa: E402

RABBITMQ_URL = "amqp://guest:guest@localhost:5672/"


def trade_payload(buyer_id: str, seller_id: str, price: str, qty: int) -> dict:
    return {
        "trade_id": str(uuid.uuid4()),
        "symbol": "AAPL",
        "price": price,
        "quantity": qty,
        "buy_order_id": str(uuid.uuid4()),
        "sell_order_id": str(uuid.uuid4()),
        "buyer_user_id": buyer_id,
        "seller_user_id": seller_id,
        "aggressor_side": "BUY",
        "buy_order_remaining": 0,
        "sell_order_remaining": 0,
        "buy_order_limit_price": price,
    }


def cancel_payload(user_id: str, order_id: str, qty: int) -> dict:
    return {
        "order_id": order_id,
        "user_id": user_id,
        "symbol": "AAPL",
        "side": "BUY",
        "cancelled_quantity": qty,
        "reason": "USER_REQUEST",
    }


def reject_payload(user_id: str, order_id: str, reason: str) -> dict:
    return {"order_id": order_id, "user_id": user_id, "symbol": "AAPL", "reason": reason}


async def publish(event_type: str, payload: dict) -> None:
    conn = await aio_pika.connect_robust(RABBITMQ_URL)
    async with conn:
        channel = await conn.channel()
        exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        env = envelope(event_type, payload)
        await exchange.publish(
            aio_pika.Message(body=json.dumps(env).encode(), content_type="application/json"),
            routing_key=event_type,
        )
        print(f"published {event_type} event_id={env['event_id']}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    kind = sys.argv[1]
    if kind == "trade":
        _, _, buyer, seller, price, qty = sys.argv
        asyncio.run(publish(TRADE_EXECUTED, trade_payload(buyer, seller, price, int(qty))))
    elif kind == "cancel":
        user_id, order_id = sys.argv[2], sys.argv[3]
        qty = int(sys.argv[4]) if len(sys.argv) > 4 else 10
        asyncio.run(publish(ORDER_CANCELLED, cancel_payload(user_id, order_id, qty)))
    elif kind == "reject":
        user_id, order_id = sys.argv[2], sys.argv[3]
        reason = sys.argv[4] if len(sys.argv) > 4 else "insufficient buying power"
        asyncio.run(publish(ORDER_REJECTED, reject_payload(user_id, order_id, reason)))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
