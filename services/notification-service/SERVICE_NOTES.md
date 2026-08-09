# Notification Service — service notes

**Port 8007 · database `notification_db` · owner: Arham Ibrahim Khan (Team A)**

Consumes trade and order events and turns them into in-app notifications plus a
mock email log line. It is a pure consumer — **nothing in the trading path ever
waits on it**, which is exactly why the proposal puts it behind the message
broker rather than calling it synchronously.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/notifications?unread_only=&limit=&offset=` | JWT | newest first, `limit` capped at 200 |
| GET | `/notifications/unread-count` | JWT | powers the navbar badge |
| POST | `/notifications/{id}/read` | JWT | 404 (not 403) if it is not yours; idempotent |
| POST | `/notifications/read-all` | JWT | single UPDATE, returns `{marked}` |
| GET | `/health` | public | liveness only, no dependency calls — Docker polls it |
| GET | `/ready` | public | checks DB + Redis + broker, 503 if degraded |

Swagger: `http://localhost:8007/docs`.

## Events consumed

| Queue | Routing key | Produces |
|---|---|---|
| `q.notification.trade_executed` | `trade.executed` | **two** notifications — one per side |
| `q.notification.order_cancelled` | `order.cancelled` | one, unless `IOC_REMAINDER` with quantity 0 |
| `q.notification.order_rejected` | `order.rejected` | one, carrying the backend reason verbatim |

Types: `ORDER_FILLED`, `ORDER_PARTIALLY_FILLED`, `ORDER_CANCELLED`, `ORDER_REJECTED`.
A side is `ORDER_FILLED` only when its `*_remaining` is 0, otherwise `ORDER_PARTIALLY_FILLED`.

## Edge cases handled

1. **Duplicate events** — idempotency is **database-first**. The authority is
   the `processed_events` primary key, inserted in the same transaction as the
   notifications. Redis is only a fast path and is written *after* the commit,
   so a crash mid-handler can never make unprocessed work look processed. The
   self-check proves the DB layer still catches a replay after a Redis flush.
2. **Side effects after the commit** (the mock email) cannot fail the handler.
   The work is already durable, so a retry would redo nothing and simply
   re-fail; the email error is logged and swallowed.
3. **Poison messages** go to `q.notification.*.retry` for 5 s and then back to
   the source queue, dead-lettering after 5 attempts. RabbitMQ does the waiting,
   so a failing handler never blocks its queue — no retry loop here.
4. **User Service unreachable** — the email lookup falls back to logging the
   user id. `retries=0`, 3 s timeout: an alert must never block on it.
5. **Long reject reasons** are truncated to 500 chars before insert.
6. **Marking another user's notification read** returns 404, which does not
   confirm whether the row exists.
7. **`read-all`** is one `UPDATE ... WHERE is_read = false`, never a loop — a
   bot user accumulates thousands of rows.
8. **Unbounded growth** — every listing is paginated and served by the
   `(user_id, created_at)` index.
9. **Unusable `user_id`** in a payload is logged and dropped rather than
   raising and wedging the queue.
10. **`IOC_REMAINDER` with quantity 0** produces nothing — nothing happened that
    the user needs to know about.

## Known limitations

- A market order sweeping ten price levels produces ten fill events and
  therefore up to twenty notifications. Coalescing was deliberately skipped:
  writing first and tidying later is safer than holding messages. Documented,
  not fixed.
- `processed_events` grows without bound. Production would prune rows older
  than about seven days.
- The email is a log line, as the proposal specifies ("mock email").

## Running it alone

```bash
docker compose -f services/notification-service/docker-compose.dev.yml up -d
```

```powershell
$env:PYTHONPATH=".;../../libs"
$env:DATABASE_URL="postgresql+asyncpg://mse:mse_pw@localhost:5439/notification_db"
$env:REDIS_URL="redis://localhost:6379/0"
$env:SERVICE_PORT="8007"
python selfcheck.py
uvicorn app.main:app --reload --port 8007
```

`selfcheck.py` runs the handlers against real Postgres and Redis, then drives
the HTTP routes through an in-process ASGI transport (so no broker is needed).
