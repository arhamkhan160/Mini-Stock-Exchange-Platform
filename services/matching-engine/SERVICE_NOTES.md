# Matching Engine — service notes

**Port 8004 · no database · owner: Abidur Rahman Asif (Team C)**

A price-time-priority limit order book, held in memory. It consumes accepted
orders, matches them, and publishes the trades. It is the only component that
knows what is actually resting, which is why it — not the Order Service — is
the authority on cancellation.

## Why there is no database

A book is a data-structure problem: two dicts of FIFO queues and an index. A
round trip to Postgres per fill would be the slowest thing in the exchange, and
the book is fully reconstructible from the Order Service's open orders. So the
book is in memory *by design* (proposal §4), and the known ceiling — a restart
loses it — is closed by the rebuild below rather than by a database.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/book/{symbol}?depth=10` | public | aggregated levels, bids descending, asks ascending |
| GET | `/book/{symbol}/trades?limit=50` | public | last 200 trades per symbol, newest first |
| GET | `/book` | public | top of book for every symbol — powers the dashboard |
| GET | `/internal/stats` | internal key | resting orders, levels, trades since start, uptime |
| GET | `/health` | public | liveness only, no dependency calls — Docker polls it |
| GET | `/ready` | public | broker only; there is no DB and no Redis here |

Swagger: `http://localhost:8004/docs`.

Every read takes the symbol lock. Without it a snapshot can serialise a deque
mid-mutation and hand the UI a book that never existed.

## Events

| Queue | Consumes | Publishes |
|---|---|---|
| `q.matching.order_accepted` | `order.accepted` | `trade.executed` (n), `order.cancelled` (IOC/no-liquidity) |
| `q.matching.cancel_requested` | `order.cancel_requested` | `order.cancelled` or `order.cancel_rejected` |

## Matching rules

* **Price priority** — best bid / lowest ask first.
* **Time priority** — FIFO within a level. A partially filled resting order goes
  back to the **front** (`appendleft`), so it keeps its place.
* **The trade price is the RESTING order's price.** The aggressor gets the price
  improvement, which is what real exchanges do. That is why `trade.executed`
  carries `buy_order_limit_price`: Account needs it to work out how much of the
  hold to give back.
* **Self-trade prevention** — an order never matches its own user's resting
  order. It is skipped and put back in place, not cancelled. Without this a user
  trades with themselves, P&L becomes nonsense, and Account would have to lock
  the same user twice inside one event.
* **Time in force** — LIMIT orders are **Good-Till-Cancelled**; MARKET orders are
  **Immediate-Or-Cancel**. A MARKET remainder is never rested; it comes back as
  `order.cancelled` with `IOC_REMAINDER`, or `NO_LIQUIDITY` if nothing filled.

## Concurrency

One `asyncio.Lock` per symbol. Matching and cancelling the same symbol are
strictly serialised; different symbols run concurrently. **Nothing is awaited
inside the matching loop** — trades are collected into a list, the lock is
released, and only then are they published. An `await` mid-match would let the
cancel consumer mutate the book underneath the loop.

This is also the reason cancellation is two-phase (`cancel_requested` → engine
removes → `cancelled`). Both paths run under the same lock, so a cancel racing a
fill is resolved one way or the other, never both.

## Edge cases handled

1. **Empty book** — LIMIT rests, MARKET publishes `NO_LIQUIDITY`. `max()` is
   never called on an empty dict.
2. **Self-trade** — skipped, never matched.
3. **A level containing only that user's orders** — drained into a holding
   deque, restored in the original order, and the scan moves to the next price.
   This is the easiest place in the project to write an infinite loop; the
   self-check covers it explicitly.
4. **Partially filled resting order** keeps its time priority.
5. **Duplicate `order.accepted`** — `order_id` in the book index *plus* a bounded
   `seen_orders` map (10 000 entries), so a redelivered *fully filled* order — no
   longer in the index — is not matched a second time.
6. **Cancel for an order the book never held** → `cancel_rejected`. `NOT_IN_BOOK`
   if we have never seen it, `ALREADY_FILLED` if `seen_orders` remembers it. We
   never publish `order.cancelled` for something the book did not hold, or
   Account would release the same funds twice.
7. **Cancel racing a fill** — same lock, so one strictly precedes the other.
8. **Crossed book** — after every match, `best_bid < best_ask` is asserted when
   both exist and an ERROR is logged if not (`DEBUG_ASSERTS`, on by default).
   Self-trade prevention is the one legitimate way to leave a crossed book.
9. **Prices are `Decimal`, never float** — `100.10` and `100.1` are one level.
10. **Quantity conservation** — `sum(fill quantities) + remaining == original`,
    asserted on every match.
11. **MARKET sweeping several levels** emits one `trade.executed` per level with
    correct running remainders.
12. **Unknown symbol** — logged, acked, dropped. A book is never created for a
    symbol outside `common.symbols.SYMBOLS`.
13. **Memory** — `recent_trades` is a `deque(maxlen=200)` per symbol and
    `seen_orders` is bounded, so the market-maker bot cannot grow them forever.

## Restart / book rebuild

On boot the engine calls `GET order-service:8003/internal/orders/open` and
re-inserts every `NEW` / `PARTIALLY_FILLED` order with
`remaining = quantity - filled_quantity`, in `created_at` order so time priority
survives. The rebuild happens **before** the consumers start, or a live
`order.accepted` could match against a half-restored book. If the call fails we
log a warning and start empty — **the engine never fails to boot**.

The other half of the same hole is closed from the Order Service side: on *its*
boot it republishes `order.accepted` for every open order, and the engine dedupes
by `order_id`. Whichever service restarts, the book ends up correct.

## Known limitations

* A publish failure after a successful match cannot be replayed — an in-memory
  book has nothing to replay *from*. It is logged as an ERROR. The upgrade path
  is a transactional outbox, which needs the database the engine deliberately
  does not have.
* Best price is an O(levels) scan rather than a heap. With 8 symbols this is
  faster and impossible to get subtly wrong; revisit past ~1000 levels.

## Running the checks

```bash
python services/matching-engine/selfcheck.py      # ~1s, no Docker, no broker
```

108 assertions: price/time priority, price improvement, self-trade prevention,
market sweeps, IOC remainders, cancellation, plus the two invariants on every
case and a field-for-field check of all three published event payloads.
