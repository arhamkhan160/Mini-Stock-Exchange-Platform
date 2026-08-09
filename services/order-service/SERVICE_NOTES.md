# Order Service — service notes

**Port 8003 · database `order_db` · owner: Abidur Rahman Asif (Team C)**

The saga orchestrator. It owns the order lifecycle and — the part that actually
matters — the compensating action for every step that can fail after somebody's
money has already been held.

## State machine

```
PENDING ──reserve ok──► NEW ──partial fill──► PARTIALLY_FILLED ──fill──► FILLED
   │                     │                          │
   │reserve fail         │cancel request            │cancel request
   ▼                     ▼                          ▼
REJECTED            CANCEL_PENDING ──engine confirms──► CANCELLED
                          │
                          └──engine says already filled──► back to FILLED / PARTIALLY_FILLED
```

`FILLED`, `CANCELLED` and `REJECTED` are terminal. **A terminal order never
changes again**, and that is enforced by a table (`models.ALLOWED`) rather than
by remembering to check: every status write goes through `service.transition()`,
which refuses any edge not in the table and writes an `order_events` audit row
for every one it allows. The audit trail *is* the proof the saga did what it says.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/orders` | JWT | 201 new, **200** when `client_order_id` replays an existing order |
| GET | `/orders?status=&symbol=&limit=50&offset=0` | JWT | own orders, newest first, `limit` capped at 200, `status` accepts a comma-separated list |
| GET | `/orders/{id}` | JWT | 404 if missing, 403 if not yours |
| DELETE | `/orders/{id}` | JWT | **202** `{"status":"CANCEL_PENDING"}` |
| GET | `/orders/{id}/events` | JWT | the audit trail |
| GET | `/internal/orders/open` | internal key | every `NEW`/`PARTIALLY_FILLED` order, for the engine's book rebuild |
| GET | `/health` `/ready` | public | `/ready` touches Postgres, Redis and the broker |

Swagger: `http://localhost:8003/docs`. Rate limiting (30/min on POST) is the
gateway's job, not this service's.

## The place-order saga

Order of operations, and why each step is where it is:

1. **Validate** — 400 on any failure, nothing persisted.
2. **Client idempotency** — `(user_id, client_order_id)` is unique. A
   double-clicked Buy button returns the *same* order with 200. Without this the
   user pays twice.
3. **Persist as `PENDING` and commit** — we need the `order_id` before we can
   reserve against it, and the row has to survive a crash mid-reservation so
   startup reconciliation can find it.
4. **Reserve** — cash from Account (BUY) or shares from Portfolio (SELL). Both
   endpoints are idempotent on `order_id`, so a retry after a timeout can never
   produce a second hold.
   * `409` → `REJECTED` with the upstream's own words, publish `order.rejected`,
     return 409 with the same detail (so "insufficient buying power" reaches the
     order ticket verbatim).
   * `503`/timeout → best-effort compensating release, `REJECTED`
     (`SERVICE_UNAVAILABLE`), return 503.
5. **`PENDING` → `NEW`**, store `reserved_amount`, commit.
6. **Publish `order.accepted`.** If that fails: **saga compensation** — release
   *both* funds and shares (both idempotent, both no-ops when nothing is held),
   `NEW` → `REJECTED` (`BROKER_UNAVAILABLE`), return 503. An order that the
   engine will never hear about must not sit there holding the user's money.
7. **201.**

MARKET buys have no limit price, so the hold is
`reference_price × quantity × 1.05` where the reference is Redis
`md:last_price:{SYMBOL}` falling back to `common.symbols.SEED_PRICES`. If
neither exists the order is rejected with `NO_MARKET_PRICE` — never reserved
for 0.

## Two-phase cancellation

`DELETE /orders/{id}` transitions to `CANCEL_PENDING`, publishes
`order.cancel_requested` and returns **202**. It does *not* release the hold.

Only the book knows whether the order was still resting when the cancel
arrived. If this service released optimistically, a fill landing in the same
millisecond would settle against a reservation that no longer exists. So the
matching engine answers:

* `order.cancelled` → `CANCELLED`
* `order.cancel_rejected` → revert `CANCEL_PENDING` to `FILLED` /
  `PARTIALLY_FILLED` / `NEW` depending on `filled_quantity`

The client briefly sees **“Cancelling…”** and the order can still come back
`FILLED`. That is correct, not a bug — the UI renders `CANCEL_PENDING` as
“Cancelling…”, never “Cancelled”.

## Idempotency: database-first

The authority is the `processed_events` primary key, inserted in the **same
transaction** as the work. Redis is only a cache: it is **read** before the work
and **written after the commit**.

Never `SET NX` before the work. A crash between the claim and the commit leaves
a marker for work that never happened; the redelivery sees "already processed",
acks, and the event is silently lost. A read-only check can only cause a
redundant retry, which the primary key stops. Prefer a duplicate over a loss.

A raising handler does not block its queue — `common.events.Broker` sends the
message to `<queue>.retry` (5s TTL, dead-letters back) and gives up to
`exchange.events.dead` after 5 attempts.

## Events

| Queue | Consumes |
|---|---|
| `q.order.trade_executed` | `trade.executed` |
| `q.order.order_cancelled` | `order.cancelled` |
| `q.order.cancel_rejected` | `order.cancel_rejected` |

Publishes `order.accepted`, `order.rejected`, `order.cancel_requested`.

Fills take the order row with `SELECT … FOR UPDATE` — two fills for the same
order can arrive back to back — and `prefetch=1` keeps one consumer per queue.
Both, not either.

## Time in force

**All LIMIT orders are Good-Till-Cancelled. All MARKET orders are
Immediate-Or-Cancel.** A MARKET order fills what it can against the book; the
remainder is cancelled by the engine (`IOC_REMAINDER`) and the excess hold is
released. There is no stop, no GTD, no partial-fill-or-kill.

## Startup reconciliation

Two holes, both closed on boot (`handlers.startup_reconciliation`, never fatal):

* **`PENDING` older than 60s** — we died between persisting and reserving.
  Release (idempotent, safe if nothing was held) and reject.
* **`NEW` / `PARTIALLY_FILLED`** — the engine holds its book in memory and may
  never have seen these. Republish `order.accepted` with `quantity` set to the
  **remaining** size, so the engine does not re-book the part that already
  traded. The engine dedupes by `order_id`, so a redundant republish is free.

## Edge cases handled

* Double-submit → same order via `client_order_id`, including the race where two
  requests get past the SELECT (the unique constraint is the real guard).
* Account 409 after a timeout where the first call actually landed → idempotent
  on `order_id`, so no double hold; `release` is still called and is a safe no-op.
* MARKET with a `price` supplied → 400, never silently ignored.
* Sub-tick `100.005`, `1e5`, `NaN`, `Infinity`, negative, `0` → 400.
* Quantity `0`, `-5`, `10.5`, `"10"`, `true` → 400 (`bool` is an `int` subclass in
  Python; `common.money.validate_quantity` already rejects it).
* Unknown or lowercase symbol → normalised, then 400 if unknown.
* Cancelling someone else's order → 403, no state change. Ownership is checked
  **before** status, so you cannot probe another user's order states.
* Double cancel → 409. Cancelling a terminal order → 409 with the actual status.
* Fills arriving out of order → `filled_quantity` is clamped to `quantity`, so
  `ck_fill_bounds` can never fail the transaction.
* A fill on an already-`CANCELLED` order still records the numbers, but the
  status stays `CANCELLED`.
* A trade naming an order we do not have → WARN and ack. Never a nack loop.
* `avg_fill_price` divide-by-zero is impossible, and guarded anyway.

## Running it alone

```bash
docker compose -f services/order-service/docker-compose.dev.yml up -d
uvicorn scripts.fake_reservations:app --port 8002 &   # stands in for Team B
uvicorn scripts.fake_reservations:app --port 8006 &   # stands in for Team D
python services/order-service/selfcheck.py            # 45 assertions, no infra
```

The self-check covers `validate()` (every 400 the order ticket can provoke) and
`transition()` (the whole state machine, including that terminal states are
terminal). The saga and the consumers need the stack; use `make up` and
`scripts/verify_stack.py`.
