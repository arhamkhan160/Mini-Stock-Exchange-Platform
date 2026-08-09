# Event Catalog

The authoritative definition lives in [`libs/common/events.py`](../libs/common/events.py).
This document explains it.

## Topology

| Thing | Value |
|---|---|
| Exchange | `exchange.events`, type `topic`, durable |
| Dead-letter exchange | `exchange.events.dead`, type `topic`, durable |
| Delivery | persistent messages, publisher confirms, `prefetch=1` |
| Retry | up to 5 attempts with exponential backoff, then dead-lettered |

Every consumer declares **its own queue**. Two services never share one, so
adding a consumer can never steal another's messages.

## Envelope

Every message on the bus:

```json
{
  "event_id": "3f2b1c9e-...",
  "event_type": "trade.executed",
  "occurred_at": "2026-08-09T12:00:00.000000Z",
  "version": 1,
  "payload": { }
}
```

`event_id` is the idempotency key. `version` exists so a payload can change
shape later without breaking old consumers.

## Producers and consumers

| Routing key | Published by | Consumed by |
|---|---|---|
| `order.accepted` | Order | Matching Engine |
| `order.rejected` | Order | Account, Portfolio, Notification |
| `order.cancel_requested` | Order | Matching Engine |
| `order.cancelled` | **Matching Engine** | Order, Account, Portfolio, Notification |
| `order.cancel_rejected` | Matching Engine | Order |
| `trade.executed` | Matching Engine | Order, Account, Portfolio, Market Data, Notification |

## Payloads

Money is a **string** with 4 decimals. Quantity is an **int**. Ids are UUID
strings. Timestamps are ISO-8601 UTC with a `Z` suffix.

```jsonc
// order.accepted
{"order_id","user_id","symbol","side":"BUY|SELL","order_type":"LIMIT|MARKET",
 "price":"195.5000"|null,          // null for MARKET
 "quantity":10,"created_at":"...Z"}

// order.rejected
{"order_id","user_id","symbol","reason","rejected_at"}

// order.cancel_requested
{"order_id","user_id","symbol","side","requested_at"}

// order.cancelled
{"order_id","user_id","symbol","side","cancelled_quantity":7,
 "reason":"USER_REQUEST|IOC_REMAINDER|NO_LIQUIDITY","cancelled_at"}

// order.cancel_rejected
{"order_id","user_id","reason":"NOT_IN_BOOK|ALREADY_FILLED","rejected_at"}

// trade.executed
{"trade_id","symbol","price":"195.5000","quantity":5,
 "buy_order_id","sell_order_id","buyer_user_id","seller_user_id",
 "aggressor_side":"BUY|SELL",
 "buy_order_remaining":0,"sell_order_remaining":3,
 "buy_order_limit_price":"196.0000"|null,   // what the buyer reserved against
 "executed_at":"...Z"}
```

### Field notes

- **`*_remaining`** is that order's unfilled quantity **after** this fill. It is
  how every consumer knows whether the order is now complete: Account releases
  the leftover hold when `buy_order_remaining` reaches 0, and Notification uses
  it to choose between `ORDER_FILLED` and `ORDER_PARTIALLY_FILLED`.
- **`buy_order_limit_price`** exists because trades execute at the *resting*
  order's price. A buyer who bid 196.00 and filled at 195.50 over-reserved, and
  Account needs the limit price to work out how much to give back.
- **`aggressor_side`** is which side crossed the spread. Market Data colours the
  trade tape with it.

## Queues

| Queue | Binding |
|---|---|
| `q.matching.order_accepted` | `order.accepted` |
| `q.matching.cancel_requested` | `order.cancel_requested` |
| `q.order.trade_executed` | `trade.executed` |
| `q.order.order_cancelled` | `order.cancelled` |
| `q.order.cancel_rejected` | `order.cancel_rejected` |
| `q.account.trade_executed` | `trade.executed` |
| `q.account.order_cancelled` | `order.cancelled` |
| `q.account.order_rejected` | `order.rejected` |
| `q.portfolio.trade_executed` | `trade.executed` |
| `q.portfolio.order_cancelled` | `order.cancelled` |
| `q.portfolio.order_rejected` | `order.rejected` |
| `q.marketdata.trade_executed` | `trade.executed` |
| `q.notification.trade_executed` | `trade.executed` |
| `q.notification.order_cancelled` | `order.cancelled` |
| `q.notification.order_rejected` | `order.rejected` |

Inspect them live at <http://localhost:15672> (guest / guest).

## Rules every consumer follows

1. **Check idempotency first.**
   ```python
   if await already_processed(redis, "portfolio", env["event_id"]):
       return
   ```
2. **Also guard in the database.** A `processed_events` primary key catches
   replays after a Redis flush, which the Redis marker alone cannot.
3. **Clear the marker on failure**, before re-raising:
   ```python
   except Exception:
       await clear_processed(redis, "portfolio", env["event_id"])
       raise
   ```
   Without this, a retried message is skipped as "already processed" and the
   work is silently lost.
4. **Never raise on an event you do not recognise.** Log it and acknowledge —
   an unknown order id or symbol must not wedge the queue.
5. **Open your own database session.** A request-scoped session is already
   closed by the time a message arrives.

## Why `order.cancelled` comes from the Matching Engine

The proposal has the Order Service publish it. In implementation that races: only
the book knows whether the order was still resting when the cancel arrived, so
releasing funds any earlier can settle a fill against a reservation that has
already been given back. Cancellation is therefore two-phase — the Order Service
publishes `order.cancel_requested`, and the engine publishes the authoritative
`order.cancelled` under the same per-symbol lock it matches with. Consumers see
exactly the events the proposal describes.
