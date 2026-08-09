"""The event contract. THE most important file in the repository.

Topology (declared by every service on startup; declarations are idempotent):
  exchange  : "exchange.events"      type=topic  durable=true
  dead      : "exchange.events.dead" type=topic  durable=true

Envelope (every message on the bus looks exactly like this):
{
  "event_id":    "<uuid4 string>",           # unique per publish, used for idempotency
  "event_type":  "trade.executed",
  "occurred_at": "2026-08-08T12:00:00.000000Z",
  "version":     1,
  "payload":     { ... }                     # see PAYLOAD SHAPES below
}

PAYLOAD SHAPES  (money = STRING with 4dp, quantity = int, ids = uuid strings)

order.accepted
  order_id, user_id, symbol, side("BUY"|"SELL"), order_type("LIMIT"|"MARKET"),
  price(string|null — null for MARKET), quantity(int), created_at(iso)

order.rejected
  order_id, user_id, symbol, reason(string), rejected_at(iso)

order.cancel_requested
  order_id, user_id, symbol, side, requested_at(iso)

order.cancelled                  <- published by the MATCHING ENGINE (book is the authority)
  order_id, user_id, symbol, side, cancelled_quantity(int),
  reason("USER_REQUEST"|"IOC_REMAINDER"|"NO_LIQUIDITY"), cancelled_at(iso)

order.cancel_rejected            <- published by the MATCHING ENGINE
  order_id, user_id, reason("NOT_IN_BOOK"|"ALREADY_FILLED"), rejected_at(iso)

trade.executed
  trade_id, symbol, price(string), quantity(int),
  buy_order_id, sell_order_id, buyer_user_id, seller_user_id,
  aggressor_side("BUY"|"SELL"),
  buy_order_remaining(int), sell_order_remaining(int),
  buy_order_limit_price(string|null),   # what the buyer had reserved against; null for MARKET
  executed_at(iso)
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import aio_pika

EXCHANGE = "exchange.events"
DEAD_EXCHANGE = "exchange.events.dead"

# --- routing keys -----------------------------------------------------------
ORDER_ACCEPTED = "order.accepted"
ORDER_REJECTED = "order.rejected"
ORDER_CANCEL_REQUESTED = "order.cancel_requested"
ORDER_CANCELLED = "order.cancelled"
ORDER_CANCEL_REJECTED = "order.cancel_rejected"
TRADE_EXECUTED = "trade.executed"

ALL_ROUTING_KEYS = [
    ORDER_ACCEPTED,
    ORDER_REJECTED,
    ORDER_CANCEL_REQUESTED,
    ORDER_CANCELLED,
    ORDER_CANCEL_REJECTED,
    TRADE_EXECUTED,
]

# --- queue names (one per consumer per event; never share a queue) ----------
Q_MATCHING_ORDER_ACCEPTED = "q.matching.order_accepted"
Q_MATCHING_CANCEL_REQUESTED = "q.matching.cancel_requested"

Q_ORDER_TRADE_EXECUTED = "q.order.trade_executed"
Q_ORDER_CANCELLED = "q.order.order_cancelled"
Q_ORDER_CANCEL_REJECTED = "q.order.cancel_rejected"

Q_ACCOUNT_TRADE_EXECUTED = "q.account.trade_executed"
Q_ACCOUNT_ORDER_CANCELLED = "q.account.order_cancelled"
Q_ACCOUNT_ORDER_REJECTED = "q.account.order_rejected"

Q_PORTFOLIO_TRADE_EXECUTED = "q.portfolio.trade_executed"
Q_PORTFOLIO_ORDER_CANCELLED = "q.portfolio.order_cancelled"
Q_PORTFOLIO_ORDER_REJECTED = "q.portfolio.order_rejected"

Q_MARKETDATA_TRADE_EXECUTED = "q.marketdata.trade_executed"

Q_NOTIFICATION_TRADE_EXECUTED = "q.notification.trade_executed"
Q_NOTIFICATION_ORDER_CANCELLED = "q.notification.order_cancelled"
Q_NOTIFICATION_ORDER_REJECTED = "q.notification.order_rejected"

MAX_DELIVERY_ATTEMPTS = 5

log = logging.getLogger(__name__)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def envelope(event_type: str, payload: dict, version: int = 1) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": utcnow_iso(),
        "version": version,
        "payload": payload,
    }


class Broker:
    """Thin aio-pika wrapper: connect with retry, publish persistent, consume
    with bounded retry then dead-letter. One instance per service."""

    def __init__(self, url: str, service_name: str = "service") -> None:
        self.url = url
        self.service_name = service_name
        self.connection: aio_pika.RobustConnection | None = None
        self.channel: aio_pika.abc.AbstractRobustChannel | None = None
        self.exchange: aio_pika.abc.AbstractExchange | None = None
        self.dead_exchange: aio_pika.abc.AbstractExchange | None = None

    async def connect(self, retries: int = 60, delay: float = 2.0) -> None:
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                self.connection = await aio_pika.connect_robust(self.url)
                self.channel = await self.connection.channel(publisher_confirms=True)
                await self.channel.set_qos(prefetch_count=1)
                self.exchange = await self.channel.declare_exchange(
                    EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
                )
                self.dead_exchange = await self.channel.declare_exchange(
                    DEAD_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
                )
                log.info("broker connected after %s attempt(s)", attempt)
                return
            except Exception as exc:  # broker not up yet during compose start
                last_error = exc
                log.warning("broker connect attempt %s failed: %s", attempt, exc)
                await asyncio.sleep(delay)
        raise RuntimeError(f"could not connect to RabbitMQ: {last_error}")

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()

    async def publish(self, routing_key: str, message: dict) -> None:
        if self.exchange is None:
            raise RuntimeError("broker not connected")
        body = json.dumps(message, separators=(",", ":")).encode()
        await self.exchange.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=message.get("event_id"),
                type=message.get("event_type"),
            ),
            routing_key=routing_key,
        )

    async def publish_event(self, event_type: str, payload: dict) -> dict:
        env = envelope(event_type, payload)
        await self.publish(event_type, env)
        return env

    async def consume(self, queue_name: str, routing_keys: list[str], handler) -> None:
        """handler: async def(envelope: dict) -> None.
        Raising from the handler retries the message (up to MAX_DELIVERY_ATTEMPTS)
        and then dead-letters it, so one poison message can never wedge a queue."""
        if self.channel is None or self.exchange is None:
            raise RuntimeError("broker not connected")
        queue = await self.channel.declare_queue(queue_name, durable=True)
        for key in routing_keys:
            await queue.bind(self.exchange, key)

        async def _on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            attempts = int((message.headers or {}).get("x-attempts", 0)) + 1
            try:
                env = json.loads(message.body)
            except json.JSONDecodeError:
                log.error("undecodable message on %s, dropping", queue_name)
                await message.ack()
                return
            try:
                await handler(env)
                await message.ack()
            except Exception:
                log.exception(
                    "handler failed on %s (attempt %s) event_id=%s",
                    queue_name, attempts, env.get("event_id"),
                )
                await message.ack()  # remove original; we re-inject or dead-letter below
                if attempts >= MAX_DELIVERY_ATTEMPTS:
                    await self.dead_exchange.publish(
                        aio_pika.Message(
                            body=message.body,
                            headers={"x-attempts": attempts, "x-origin-queue": queue_name},
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        ),
                        routing_key=queue_name,
                    )
                    return
                await asyncio.sleep(min(2 ** attempts, 10))
                await self.channel.default_exchange.publish(
                    aio_pika.Message(
                        body=message.body,
                        headers={"x-attempts": attempts},
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        message_id=env.get("event_id"),
                    ),
                    routing_key=queue_name,  # straight back onto this queue only
                )

        await queue.consume(_on_message)
        log.info("consuming %s <- %s", queue_name, routing_keys)
