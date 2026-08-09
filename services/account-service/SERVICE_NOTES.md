# Account Service — service notes

**Port 8002 · database `account_db` · owner: Arham Apon Utsho (Team B)**

**The most consistency-critical service in the system.** Virtual cash:
balances, deposits, the transaction ledger, and the funds-hold primitives
Order calls synchronously before accepting a BUY order. Settlement itself is
event-driven (`trade.executed`, `order.cancelled`, `order.rejected`), so a
slow or down Matching Engine never blocks a deposit or a balance check.

## The balance model

```
cash_balance      total virtual cash the user owns
held_balance      the part reserved against open BUY orders
available_balance = cash_balance - held_balance     <- this IS buying power
```

Enforced by DB `CHECK` constraints (`ck_cash_non_negative`,
`ck_held_non_negative`, `ck_held_le_cash`): if a bug ever breaks one of these
invariants, Postgres aborts the transaction instead of silently corrupting
money.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/account/balance` | JWT | get-or-creates a zero-balance account |
| POST | `/account/deposit` | JWT | `0 < amount <= 1,000,000`, at most 2 decimal places |
| GET | `/account/transactions?limit=&offset=&type=` | JWT | newest first, `limit` capped at 200, optional type filter |
| GET | `/internal/accounts/{user_id}` | `X-Internal-Key` | any user's balance, for debugging |
| POST | `/internal/reservations` | `X-Internal-Key` | `{order_id, user_id, amount}`; idempotent on `order_id` |
| POST | `/internal/reservations/{order_id}/release` | `X-Internal-Key` | releases whatever remains; 404 only if `order_id` was never reserved |
| GET | `/health` | public | liveness only, no dependency calls |
| GET | `/ready` | public | checks DB + Redis + broker, 503 if degraded |

Swagger: `http://localhost:8002/docs`. Bad request bodies return **400**
(see `main.py`'s `RequestValidationError` handler — same pattern as User
Service), not FastAPI's default 422.

## Events consumed

| Queue | Routing key | Effect |
|---|---|---|
| `q.account.trade_executed` | `trade.executed` | buyer: charge execution price, consume/release the reservation; seller: credit proceeds |
| `q.account.order_cancelled` | `order.cancelled` | release whatever remains of the order's hold |
| `q.account.order_rejected` | `order.rejected` | same as cancelled |

## The reservation model

`Reservation` rows are **never deleted** — one row per order that has ever
held buyer cash, advancing through `status`: `HELD` → `CONSUMED` (fully used
by fills, nothing released) or `RELEASED` (some or all of it freed by a
cancel/reject or a finishing fill with slack left). `remaining = amount_held
- amount_consumed - amount_released`. Keeping the row permanently is what
lets the release endpoint tell "already resolved" (200, `released:"0.0000"`)
apart from "never reserved at all" (404) — a deleted row would look identical
to a never-created one.

**Settling a fill:** the buyer is always charged `execution_price * quantity`
in cash. The amount *consumed* from the reservation is
`min(reserved_price * quantity, remaining_hold)`, where `reserved_price` is
the order's limit price when known. A LIMIT buy filled below its limit
consumes less than it pays for from the hold, releasing the price-improvement
back to `available_balance`; a MARKET buy (no limit price) has no
improvement to compute, so `reserved_price` falls back to the execution
price. On the fill that empties the order (`buy_order_remaining == 0`), any
slack still left in the reservation is released too — this is what actually
closes out a MARKET order's hold, and is a no-op (releases 0) when a LIMIT
order's math already worked out to exactly zero remaining.

Worked example (also in `selfcheck.py`): deposit 10000, reserve 2000 for
order X → held 2000. Fill 5 shares @ 200 (limit 210, 5 remaining) → consumed
= min(210×5, 2000) = 1050 → cash 9000, held 950. Final fill, 5 more @ 200
(limit 210, 0 remaining) → consumed = min(210×5, 950) = 950 (capped) → cash
8000, held 0, nothing left to release.

## Locking — the graded showpiece

Every read-modify-write of an Account/Reservation row is wrapped in
`app.locking.funds_lock(redis, user_id)`, a thin wrapper around
`common.redis_client.distributed_lock` (`lock:funds:{user_id}`, the contract's
key) that **logs every acquire and release with the user id** —
`grep "lock acquired"` on this service's logs is the demo evidence for the
distributed-locking requirement. It fails closed: both a lock timeout and
Redis itself being unreachable raise `FundsLockUnavailable`, which every REST
endpoint maps to **503** (never 500 — reserving or settling money without the
lock held is not safe) and every event handler lets propagate so the
broker's retry-with-backoff handles it. A Postgres `SELECT ... FOR UPDATE`
underneath the Redis lock is what actually stops two transactions from both
reading a stale balance; the Redis lock is what makes acquire/release visible
and orders concurrent REST calls before either commits.

**Self-trade** (buyer and seller are the same user, e.g. the market-maker
crossing itself) locks the *set* of distinct user ids rather than each side
separately — locking the same Redis key twice from one task would deadlock
against itself. RabbitMQ delivers each queue to this service with
`prefetch=1`, so at most one event handler runs at a time; there is no
cross-trade lock-ordering hazard to worry about on top of that.

## Edge cases handled

1. **Idempotency is database-first**, exactly per the shared contract: the
   `processed_events` primary key is the authority, inserted in the same
   transaction as the balance changes; the Redis marker is read before the
   work and written only *after* the commit. `selfcheck.py` proves a replay
   is caught even after `FLUSHDB`.
2. **Redis down** → `funds_lock` raises `FundsLockUnavailable` → 503. Fail
   closed; reserving without the lock could create money from nothing.
3. **Lock expiry mid-transaction** — TTL is 5s; a slow DB could in principle
   outlive it. That is why the `SELECT ... FOR UPDATE` row lock and the
   `CHECK` constraints exist underneath the Redis lock: the Redis lock is for
   throughput and visibility, the DB is for correctness.
4. **Releasing a lock you no longer own** is handled by the Lua
   compare-and-delete inside `common.redis_client.distributed_lock` — this
   service never calls `DEL` directly.
5. **Reserving more than `available_balance`** is a 409, not a silent
   overdraft.
6. **Re-reserving the same `order_id`** (a retried call after a dropped
   response) returns the existing hold instead of doubling it — including
   after the reservation has since moved to `CONSUMED`/`RELEASED`.
7. **Releasing an `order_id` that was never reserved** is a 404. Releasing
   one that already resolved (nothing left, or a SELL order that never held
   cash at all) is a 200 with `released:"0.0000"` — release must be safe to
   call more than once.
8. **`held_balance` is clamped at zero** on every release; rounding or an
   out-of-order event can never push it negative (the `CHECK` constraint
   would catch it anyway, but the clamp keeps that from ever firing).
9. **Deposit amount validation**: non-numeric, non-positive, over the
   1,000,000 cap, or more than 2 decimal places are all 400s before touching
   the database. The 2-decimal-place rule matches the frontend's
   `validateAmount` — a deposit is dollars-and-cents, unlike a computed
   reservation amount (price × quantity), which legitimately uses the full
   4dp of internal precision.
10. **Account auto-creation race** — `get_or_create_account` is always called
    inside `funds_lock`, including the new `GET /internal/accounts/{id}`
    debug endpoint, so two concurrent first-touches for the same never-seen
    user_id can't both try to INSERT the same row.

## Known limitations

- `processed_events` grows without bound; production would prune rows older
  than a few days.
- No withdrawal endpoint — the proposal only calls for deposits.
- A MARKET buy's reservation amount is whatever Order decided to hold when it
  called `/internal/reservations`; this service has no opinion on how that
  number was computed.

## Running it alone

```bash
docker compose -f docker-compose.dev.yml up -d
```

```powershell
$env:PYTHONPATH=".;../../libs"
$env:DATABASE_URL="postgresql+asyncpg://mse:mse_pw@localhost:5434/account_db"
$env:REDIS_URL="redis://localhost:6379/0"
$env:SERVICE_PORT="8002"
$env:SERVICE_NAME="account-service"
python selfcheck.py
uvicorn app.main:app --reload --port 8002
```

`selfcheck.py` calls the event handlers directly against real Postgres and
Redis (no RabbitMQ needed) and drives the HTTP routes, including the internal
reservation endpoints, through an in-process ASGI transport. To fake a real
Matching Engine event over the wire instead, use `scripts/emit_event.py`
(needs `docker-compose.dev.yml`'s rabbitmq running):

```bash
python scripts/emit_event.py trade.executed '{"trade_id":"...","symbol":"AAPL",...}'
```
