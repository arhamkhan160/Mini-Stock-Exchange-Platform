# Account Service — service notes

**Port 8002 · database `account_db` · owner: Arham Apon Utsho (Team B)**

Virtual cash: balances, deposits, the transaction ledger, and the funds-hold
primitives Order calls synchronously before accepting a BUY order. Settlement
itself is event-driven (`trade.executed`, `order.cancelled`, `order.rejected`),
so a slow or down Matching Engine never blocks a deposit or a balance check.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/account/balance` | JWT | get-or-creates a zero-balance account |
| POST | `/account/deposit` | JWT | amount `> 0`, `<= 1,000,000` |
| GET | `/account/transactions?limit=&offset=` | JWT | newest first, `limit` capped at 200 |
| POST | `/internal/reservations` | `X-Internal-Key` | body `{order_id, user_id, amount}`; idempotent on `order_id` |
| POST | `/internal/reservations/{order_id}/release` | `X-Internal-Key` | body `{amount?}` — omit to release everything left; idempotent, no-op if nothing is held |
| GET | `/health` | public | liveness only, no dependency calls |
| GET | `/ready` | public | checks DB + Redis + broker, 503 if degraded |

Swagger: `http://localhost:8002/docs`.

## Events consumed

| Queue | Routing key | Effect |
|---|---|---|
| `q.account.trade_executed` | `trade.executed` | buyer: charge execution price, consume/release the reservation; seller: credit proceeds |
| `q.account.order_cancelled` | `order.cancelled` | release whatever remains of the order's hold |
| `q.account.order_rejected` | `order.rejected` | same as cancelled |

## The reservation model

One `Reservation` row per order that is currently holding buyer cash,
keyed by `order_id`. A missing row means "nothing held for that order" and
every release path treats that as a successful no-op, never a 404 — an order
that was a SELL (shares are held by Portfolio, not here) or that already
settled looks identical to one that never existed, and both are fine outcomes.

**Settling a fill:** the buyer is always charged `execution_price * quantity`
in cash, regardless of what was held. The amount *released* from the
reservation is `min(reserved_price * quantity, reservation.amount)`, where
`reserved_price` is the order's limit price when known. This means a LIMIT
buy filled below its limit releases the price-improvement back to
`available_balance` immediately; a MARKET buy (no limit price) has no
improvement to compute, so the reservation is assumed consumed at cost. On
the fill that empties the order (`buy_order_remaining == 0`), any slack still
left in the reservation is released too, which is what actually closes out a
MARKET order's hold.

## Edge cases handled

1. **Idempotency is database-first**, exactly per the shared contract: the
   `processed_events` primary key is the authority, inserted in the same
   transaction as the balance changes; the Redis marker is read before the
   work and written only *after* the commit. The self-check proves a replay
   is caught even after `FLUSHDB`.
2. **Concurrent mutation of the same account** is guarded twice: the Redis
   `lock:funds:{user_id}` (the contract's key) around every REST call and
   event handler, and a Postgres `SELECT ... FOR UPDATE` on the account row
   underneath it — belt and suspenders, since the row lock is what actually
   stops two transactions from reading a stale balance.
3. **Self-trade** (buyer and seller are the same user) locks the *set* of
   distinct user ids rather than each side separately — locking the same
   Redis key twice from one task would deadlock against itself.
4. **Reserving more than `available_balance`** (`cash_balance - held_balance`)
   is a 409, not a silent overdraft.
5. **Re-reserving the same `order_id`** (a retried call after a dropped
   response) returns the existing hold instead of doubling it.
6. **Releasing a reservation that does not exist** — already released, or an
   order type that never held cash — is a 200 no-op, because release must be
   safe to call more than once.
7. **held_balance is clamped at zero** on every release; rounding or an
   out-of-order event can never push it negative.
8. **`DepositRequest`/reservation amounts** are validated through
   `common.money` — non-numeric, non-positive, or over-limit amounts are 422s
   before touching the database.

## Known limitations

- `processed_events` grows without bound; production would prune rows older
  than a few days.
- No withdrawal endpoint — the proposal only calls for deposits.
- A MARKET buy's reservation amount is whatever Order decided to hold when it
  called `/internal/reservations`; this service has no opinion on how that
  number was computed.

## Running it alone

```bash
docker compose up -d postgres-account redis rabbitmq
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
reservation endpoints, through an in-process ASGI transport.
