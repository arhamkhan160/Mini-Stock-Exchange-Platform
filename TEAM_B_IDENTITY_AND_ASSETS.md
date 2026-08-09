# TEAM B — User Service · Account Service · Auth & Wallet UI

**Owner: Arham Apon Utsho (220042153)**
**Your services: User Service (8001) and Account Service (8002) — identity and virtual cash.**

> Give this whole file to Claude Code in the repo root and say:
> *"Read TEAM_B_IDENTITY_AND_ASSETS.md and implement my sections completely. `libs/common/` and the `frontend/` shell already exist — read them, import from them, do not rewrite them."*

**You are blocked by nobody.** Neither service makes an outbound call to another team's service. You expose REST and consume RabbitMQ events; you fake the events with a 10-line script (§6).

---

# SECTION 1 — THE SHARED CONTRACT
*(Sections 1.1–1.13 are byte-identical in all four team documents. Never change them unilaterally — message the group first.)*

## 1.1 Who builds what (roughly equal load, everyone owns real services)

| | Person | Services owned | Also owns | Est. hours |
|---|---|---|---|---|
| **A** | Arham Ibrahim Khan (220042160) | **Notification (8007)**, **API Gateway (8000)** | Docker Compose + all infra + Postgres replication, `/notifications` UI, docs & diagrams, **final merge** | ~15.5 |
| **B** | Arham Apon Utsho (220042153) | **User (8001)**, **Account (8002)** | login/register/wallet/dashboard UI, `scripts/smoke_test.py`, market-maker bot | ~13.5 |
| **C** | Abidur Rahman Asif (220042152) | **Order (8003)**, **Matching Engine (8004)** | orders UI + OrderTicket/OrderBook/RecentTrades components | ~13.5 |
| **D** | Mustain Billah Taj (220042166) | **Market Data (8005)**, **Portfolio (8006)** | market page + candlestick chart + portfolio UI, candle seeder, mock API | ~15 |

## 1.2 Ownership map — nobody edits anybody else's files

| Path | Owner |
|---|---|
| `libs/common/**` | **pre-built, frozen** (A patches it if needed) |
| `frontend/lib/**`, `frontend/app/layout.tsx`, `frontend/app/globals.css`, `frontend/components/{ui,Toast,Protected,Navbar}.tsx`, `frontend/package.json`, configs | **pre-built, frozen** |
| `requirements-base.txt`, `.gitattributes`, `.dockerignore` | **pre-built** |
| `docker-compose.yml`, `.env`, `Makefile`, `infra/postgres/**`, `docs/**`, `README.md` | A |
| `services/gateway/**`, `services/notification-service/**` | A |
| `frontend/app/notifications/page.tsx`, `frontend/components/NotificationBell.tsx` | A |
| **`services/user-service/**`, `services/account-service/**`** | **B** |
| **`frontend/app/{login,register,wallet}/page.tsx`, `frontend/app/page.tsx`, `frontend/components/{SymbolTable,DepositForm}.tsx`** | **B** |
| **`scripts/smoke_test.py`, `infra/seed/market_maker.py`** | **B** |
| `services/order-service/**`, `services/matching-engine/**` | C |
| `frontend/app/orders/page.tsx`, `frontend/components/{OrderTicket,OrderBook,RecentTrades,OrdersTable}.tsx` | C |
| `services/market-data-service/**`, `services/portfolio-service/**` | D |
| `frontend/app/market/[symbol]/page.tsx`, `frontend/app/portfolio/page.tsx`, `frontend/components/{CandleChart,HoldingsTable}.tsx` | D |
| `infra/seed/seed_market_data.py`, `frontend/mock-api.py` | D |

## 1.3 Ports

| Component | Container | Port | Host |
|---|---|---|---|
| Frontend | `frontend` | 3000 | 3000 |
| API Gateway | `gateway` | 8000 | 8000 |
| **User** | `user-service` | **8001** | 8001 |
| **Account** | `account-service` | **8002** | 8002 |
| Order | `order-service` | 8003 | 8003 |
| Matching Engine | `matching-engine` | 8004 | 8004 |
| Market Data | `market-data-service` | 8005 | 8005 |
| Portfolio | `portfolio-service` | 8006 | 8006 |
| Notification | `notification-service` | 8007 | 8007 |
| Postgres user / account / order | `postgres-user` / `-account` / `-order` | 5432 | 5433 / 5434 / 5435 |
| Postgres market PRIMARY / REPLICA | `postgres-market-primary` / `-replica` | 5432 | 5436 / 5437 |
| Postgres portfolio / notification | `postgres-portfolio` / `-notification` | 5432 | 5438 / 5439 |
| Redis | `redis` | 6379 | 6379 |
| RabbitMQ | `rabbitmq` | 5672 / 15672 | 5672 / 15672 |

## 1.4 Money & quantity rules

* Money is `Decimal`, 4 dp, `ROUND_HALF_UP`. **Never float.** Postgres `NUMERIC(18,4)`.
* Money crosses the wire as a **JSON string**: `"195.5000"`. *(One documented exception: `/api/market/candles` returns numbers for the chart.)*
* Quantity is a positive **int**. Postgres `BIGINT`.
* Limit price must be a positive multiple of `0.01`.
* Use `common.money`: `to_money`, `money_str`, `validate_price`, `validate_quantity`, `notional`.
* Symbols: `common.symbols.SYMBOLS`, `SEED_PRICES`, `normalize_symbol`.

## 1.5 Auth

JWT HS256, secret `JWT_SECRET` identical in every container. Claims: `sub` (user id UUID string), `email`, `username`, `iat`, `exp`, `type:"access"`. Expiry 1440 min.
Gateway validates and forwards `Authorization` unchanged; every service validates again via `common.security.get_current_user`.
`/internal/*` requires `X-Internal-Key` (`Depends(require_internal_key)`); the gateway refuses to proxy any path containing `/internal/`.

## 1.6 Gateway routing table

Strip `/api`, keep the rest of the path and the query string.

| Method | Public path | Upstream | Auth |
|---|---|---|---|
| POST | `/api/auth/register` | user-service | public |
| POST | `/api/auth/login` (form-encoded) | user-service | public |
| POST | `/api/auth/login-json` | user-service | public |
| GET/PATCH | `/api/users/me` | user-service | JWT |
| GET | `/api/account/balance` | account-service | JWT |
| POST | `/api/account/deposit` | account-service | JWT |
| GET | `/api/account/transactions` | account-service | JWT |
| POST | `/api/orders` | order-service | JWT, 30/min |
| GET | `/api/orders`, `/api/orders/{id}` | order-service | JWT |
| DELETE | `/api/orders/{id}` | order-service | JWT |
| GET | `/api/book/{symbol}` | matching-engine | public |
| GET | `/api/market/symbols`, `/quote/{s}`, `/candles/{s}`, `/trades/{s}` | market-data-service | public |
| GET | `/api/portfolio`, `/api/portfolio/pnl` | portfolio-service | JWT |
| GET | `/api/notifications`, `/api/notifications/unread-count` | notification-service | JWT |
| POST | `/api/notifications/{id}/read`, `/api/notifications/read-all` | notification-service | JWT |
| WS | `/ws/market` | market-data-service | public |

Errors everywhere: `{"detail": "message"}` with a real status — 400 validation, 401 auth, 403 not yours, 404 missing, 409 business conflict, 429 rate limited, 503/504 upstream.

## 1.7 Event contract

Exchange `exchange.events` (topic, durable). Dead-letter `exchange.events.dead`. Envelope:
```json
{ "event_id":"<uuid4>", "event_type":"trade.executed",
  "occurred_at":"2026-08-08T12:00:00.000000Z", "version":1, "payload":{ } }
```

| Routing key | Published by | **You consume?** |
|---|---|---|
| `order.accepted` | Order (C) | no |
| `order.rejected` | Order (C) | **yes — Account releases the reservation** |
| `order.cancel_requested` | Order (C) | no |
| `order.cancelled` | Matching Engine (C) | **yes — Account releases the reservation** |
| `order.cancel_rejected` | Matching Engine (C) | no |
| `trade.executed` | Matching Engine (C) | **yes — Account settles cash both sides** |

```jsonc
// order.rejected
{"order_id","user_id","symbol","reason","rejected_at"}
// order.cancelled
{"order_id","user_id","symbol","side","cancelled_quantity":7,
 "reason":"USER_REQUEST|IOC_REMAINDER|NO_LIQUIDITY","cancelled_at"}
// trade.executed
{"trade_id","symbol","price":"195.5000","quantity":5,
 "buy_order_id","sell_order_id","buyer_user_id","seller_user_id",
 "aggressor_side":"BUY|SELL","buy_order_remaining":0,"sell_order_remaining":3,
 "buy_order_limit_price":"196.0000"|null,"executed_at"}
```
Your queues: `q.account.trade_executed`, `q.account.order_cancelled`, `q.account.order_rejected`.

## 1.8 Redis keys
```
lock:funds:{user_id}            distributed lock, SET NX PX 5000, Lua compare-and-delete release
lock:shares:{user_id}:{SYMBOL}  (Portfolio's — not yours)
idem:{service}:{event_id}       SET NX EX 86400 — event idempotency
md:last_price:{SYMBOL}          written by Market Data
ratelimit:{identity}:{minute}   gateway
CHANNEL md:ticks
```
Helpers: `common.redis_client.distributed_lock`, `already_processed`, `clear_processed`.

## 1.9 Internal service-to-service REST
```
POST account-service:8002 /internal/reservations                     <- YOU IMPLEMENT
POST account-service:8002 /internal/reservations/{order_id}/release  <- YOU IMPLEMENT
POST portfolio-service:8006 /internal/share-reservations             (D implements)
POST portfolio-service:8006 /internal/share-reservations/{id}/release
GET  order-service:8003 /internal/orders/open                        (C implements)
GET  user-service:8001  /internal/users/{id}                         <- YOU IMPLEMENT
```

## 1.10 Every service exposes
`GET /health` → `{"status":"ok","service":"<name>"}`, no DB call. `GET /ready` → DB + Redis + broker, 503 if any down. Swagger `/docs`, `version="1.0.0"`.

## 1.11 Python service templates (build context is the REPO ROOT)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app:/app/libs
COPY requirements-base.txt ./
COPY services/<NAME>/requirements.txt ./service-requirements.txt
RUN pip install --no-cache-dir -r service-requirements.txt
COPY libs /app/libs
COPY services/<NAME>/app /app/app
COPY services/<NAME>/alembic.ini /app/alembic.ini
COPY services/<NAME>/alembic /app/alembic
COPY services/<NAME>/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
EXPOSE <PORT>
CMD ["/app/entrypoint.sh"]
```
`requirements.txt` = `-r ../../requirements-base.txt`. `entrypoint.sh` — **LF only**:
```bash
#!/bin/sh
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${SERVICE_PORT}"
```
Alembic `env.py`:
```python
from common.config import settings
from common.db import Base, sync_url
from app.models import *  # noqa
config.set_main_option("sqlalchemy.url", sync_url(settings.DATABASE_URL))
target_metadata = Base.metadata
```
**Hand-write** `versions/0001_initial.py`.

## 1.12 Frontend — the shell is already built

Frozen and ready: `lib/api.ts` (typed client for every route + 401 handling), `lib/auth.tsx` (`useAuth()`), `lib/ws.ts` (`useLivePrices()`), `lib/format.ts` (`money`, `price`, `qty`, `pct`, `toneOf`, `timeAgo`, `validatePrice`, `validateQuantity`, `validateAmount`), `components/ui.tsx` (`Card`, `Button`, `Input`, `Select`, `Badge`, `Tone`, `Table`, `Empty`, `Spinner`, `ErrorBox`), `components/Toast.tsx` (`useToast()`), `components/Protected.tsx`, `Navbar.tsx`, `app/layout.tsx`, dark theme tokens.

**Use these primitives — do not invent new button/card styles.**

| Route / component | Owner |
|---|---|
| **`app/page.tsx` (dashboard), `app/login`, `app/register`, `app/wallet`, `components/SymbolTable.tsx`, `components/DepositForm.tsx`** | **B** |
| `app/orders/page.tsx`, `components/OrderTicket.tsx`, `OrderBook.tsx`, `RecentTrades.tsx`, `OrdersTable.tsx` | C |
| `app/market/[symbol]/page.tsx`, `app/portfolio/page.tsx`, `components/CandleChart.tsx`, `HoldingsTable.tsx` | D |
| `app/notifications/page.tsx`, `components/NotificationBell.tsx` | A |

## 1.13 Merge protocol
* Your branch: **`feat/team-b-identity`**. A works on `main`.
* Nobody edits `libs/common`, the frontend shell, `docker-compose.yml`, `.env`, or another team's folder.
* Push at least every 3 hours even if incomplete.
* Deliver in your folders: `Dockerfile`, `requirements.txt`, `entrypoint.sh`, `alembic/`, `selfcheck.py`, `SERVICE_NOTES.md`.
* Merge windows: **hour 13** and **hour 18**.

---

# SECTION 2 — YOUR SERVICE 1: USER SERVICE (port 8001, `user_db`)

```
services/user-service/
  app/{main.py,models.py,schemas.py,routes_auth.py,routes_users.py,passwords.py,deps.py}
  alembic/{env.py,versions/0001_initial.py}   alembic.ini
  requirements.txt Dockerfile entrypoint.sh docker-compose.dev.yml selfcheck.py SERVICE_NOTES.md
```

## 2.1 Model
```python
class User(Base):
    __tablename__ = "users"
    id            = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email         = mapped_column(String(255), nullable=False, unique=True, index=True)  # LOWERCASE
    username      = mapped_column(String(50),  nullable=False, unique=True, index=True)
    password_hash = mapped_column(String(255), nullable=False)
    full_name     = mapped_column(String(120), nullable=True)
    is_active     = mapped_column(Boolean, nullable=False, default=True)
    created_at    = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at    = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

## 2.2 Passwords — use `bcrypt` directly, **not** passlib
```python
import bcrypt
MAX_PASSWORD_BYTES = 72          # bcrypt SILENTLY truncates past 72 bytes — that is an auth bypass

def hash_password(raw: str) -> str:
    data = raw.encode("utf-8")
    if len(data) > MAX_PASSWORD_BYTES:
        raise ValueError("password too long (max 72 bytes)")
    return bcrypt.hashpw(data, bcrypt.gensalt(rounds=12)).decode()

def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8")[:MAX_PASSWORD_BYTES], hashed.encode())
    except ValueError:
        return False              # a corrupt hash in the DB must not 500
```
*(passlib 1.7.4 crashes against bcrypt ≥ 4.1 — that is why it is not in `requirements-base.txt`.)*

## 2.3 Endpoints

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/register` | `{email, username, password, full_name?}` → **201** `{user, access_token, token_type:"bearer", expires_in}` |
| POST | `/auth/login` | **OAuth2 password flow** — `OAuth2PasswordRequestForm`, form-encoded, fields `username` (accepts email **or** username) + `password` → `{access_token, token_type, expires_in, user}` |
| POST | `/auth/login-json` | same, JSON `{email_or_username, password}` |
| GET | `/users/me` | JWT |
| PATCH | `/users/me` | `{full_name?, username?}` |
| POST | `/auth/change-password` | `{current_password, new_password}` |
| GET | `/internal/users/{user_id}` | internal key → `{id,email,username,full_name}` (Notification uses it for mock email) |
| GET | `/internal/users?ids=a,b,c` | batch, max 100 ids |

Validation: `EmailStr`; username `^[a-zA-Z0-9_]{3,30}$`; password 8–72 bytes with at least one letter and one digit. **Never return `password_hash`** — define a `UserOut` model and use it everywhere.

## 2.4 Edge cases — User Service

1. **Duplicate email differing only in case** (`Arham@x.com` vs `arham@x.com`) → lowercase in a pydantic validator *before* the DB write; store lowercase. Username uniqueness is checked case-insensitively (`func.lower`).
2. **Concurrent registration race** — two requests pass the "exists?" `SELECT` at once. **Do not trust the pre-check**: wrap the `INSERT` in `try/except IntegrityError` → rollback → 409. The DB unique constraint is what actually enforces it.
3. **Password > 72 bytes** (or emoji pushing it past 72) → 400 before hashing. Never let bcrypt truncate silently.
4. **Wrong password vs unknown user must be indistinguishable** — same 401 body, and run a dummy `checkpw` against a constant hash when the user doesn't exist so response time doesn't leak account existence.
5. **Inactive user** → 403 `{"detail":"account disabled"}`.
6. **`sub` must be a string** — PyJWT ≥ 2.10 rejects a non-string subject. `create_access_token` already does `str(user_id)`; never bypass it.
7. **Login with uppercase email** → lowercase the identifier before lookup.
8. **Username-change collision** → 409 via the same `IntegrityError` path.
9. **`OAuth2PasswordRequestForm` needs `python-multipart`** — it is in `requirements-base.txt`. If login 500s with "Form data requires python-multipart", that's why.
10. **Whitespace-only `full_name`** → store `NULL`, not `""`.
11. **Token for a deleted/disabled user** — `get_current_user` only decodes the JWT (no DB hit, by design). Endpoints that *mutate* the user re-load the row and 401 if missing or inactive.
12. **No account row is created here.** Account Service auto-creates a zero-balance account on first touch — this is what keeps User Service dependency-free. Note it in `SERVICE_NOTES.md`.

---

# SECTION 3 — YOUR SERVICE 2: ACCOUNT SERVICE (port 8002, `account_db`)

**The most consistency-critical service in the system.** The proposal specifically calls out Redis distributed locking and short-lived funds reservations here — make the locking visible in the logs, it is a graded showpiece.

## 3.1 Balance model — learn this before writing code
```
cash_balance      total virtual cash the user owns
held_balance      the part reserved against open BUY orders
available_balance = cash_balance - held_balance      <- this IS buying power
```
DB `CHECK` constraints: `cash_balance >= 0`, `held_balance >= 0`, `held_balance <= cash_balance`. If a bug ever breaks the invariant, Postgres aborts the transaction instead of silently corrupting money.

## 3.2 Models
```python
class Account(Base):
    __tablename__ = "accounts"
    user_id      = mapped_column(UUID(as_uuid=True), primary_key=True)
    cash_balance = mapped_column(Numeric(18,4), nullable=False, default=Decimal("0"))
    held_balance = mapped_column(Numeric(18,4), nullable=False, default=Decimal("0"))
    currency     = mapped_column(String(3), nullable=False, default="USD")
    created_at / updated_at
    __table_args__ = (
        CheckConstraint("cash_balance >= 0", name="ck_cash_non_negative"),
        CheckConstraint("held_balance >= 0", name="ck_held_non_negative"),
        CheckConstraint("held_balance <= cash_balance", name="ck_held_le_cash"),
    )

class Reservation(Base):                 # one per BUY order
    __tablename__ = "reservations"
    order_id        = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id         = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    amount_held     = mapped_column(Numeric(18,4), nullable=False)
    amount_consumed = mapped_column(Numeric(18,4), nullable=False, default=0)
    amount_released = mapped_column(Numeric(18,4), nullable=False, default=0)
    status          = mapped_column(String(16), nullable=False, default="HELD")  # HELD|CONSUMED|RELEASED
    created_at / updated_at
    # remaining = amount_held - amount_consumed - amount_released

class Transaction(Base):
    __tablename__ = "transactions"
    id = uuid pk; user_id = UUID index
    type          = String(16)     # DEPOSIT | HOLD | RELEASE | TRADE_BUY | TRADE_SELL
    amount        = Numeric(18,4)  # signed
    balance_after = Numeric(18,4)
    reference_id  = UUID null      # order_id or trade_id
    description   = String(255)
    created_at    = DateTime index
    __table_args__ = (Index("ix_tx_user_created", "user_id", "created_at"),)

class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    event_id = UUID pk; event_type = String(64); handled_at
```

## 3.3 Public endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/account/balance` | JWT, auto-creates the account row → `{user_id, cash_balance, held_balance, available_balance, currency}` (strings) |
| POST | `/account/deposit` | JWT, `{amount:"10000.00"}`, `0 < amount <= 1000000`, ≤ 4 dp |
| GET | `/account/transactions?limit=50&offset=0&type=` | JWT, newest first, `limit` capped at 200 |
| GET | `/internal/accounts/{user_id}` | internal key, debugging |

## 3.4 Internal endpoints (Order Service calls these — §1.9 is the frozen contract)

`POST /internal/reservations`:
```
async with distributed_lock(redis, f"lock:funds:{user_id}", ttl_ms=5000, wait_seconds=5):
    async with session.begin():
        existing = SELECT reservation WHERE order_id = :order_id FOR UPDATE
        if existing: return 201 with existing                  # idempotent replay
        account = SELECT * FROM accounts WHERE user_id = :uid FOR UPDATE  # create if missing
        if account.cash_balance - account.held_balance < amount:
            return 409 {"detail": "insufficient buying power"}
        account.held_balance += amount
        insert Reservation(order_id, user_id, amount_held=amount, status="HELD")
        insert Transaction(type="HOLD", amount=-amount, balance_after=available_after, reference_id=order_id)
```
`LockTimeout` → **503**. Never 500, and **never proceed without the lock**.

`POST /internal/reservations/{order_id}/release`:
```
lock -> load reservation FOR UPDATE
remaining = amount_held - amount_consumed - amount_released
if remaining <= 0: return {"released":"0.0000"}     # idempotent, still 200
account.held_balance = max(Decimal(0), account.held_balance - remaining)
reservation.amount_released += remaining
reservation.status = "RELEASED"
insert Transaction(type="RELEASE", amount=remaining, reference_id=order_id)
```
404 only if the `order_id` was never reserved at all.

## 3.5 Event handlers

**`trade.executed`** — one event settles **both** sides:
```
if await already_processed(redis, "account", event_id): ack, return
notional = to_money(price) * quantity

# --- BUYER ---
lock(lock:funds:{buyer_user_id}):
    reservation = SELECT ... WHERE order_id = buy_order_id FOR UPDATE
    if reservation is None:
        log ERROR "settling a buy with no reservation"     # should not happen; debit if funds allow
        consume = min(notional, account.cash_balance)
    else:
        remaining_hold = amount_held - amount_consumed - amount_released
        consume = min(notional, remaining_hold)
        reservation.amount_consumed += consume
    account.cash_balance -= notional
    account.held_balance  = max(Decimal(0), account.held_balance - consume)
    Transaction(type="TRADE_BUY", amount=-notional, reference_id=trade_id)
    if buy_order_remaining == 0 and reservation:
        release_remaining(reservation)      # price improvement / MARKET over-reservation comes back here

# --- SELLER ---
lock(lock:funds:{seller_user_id}):
    account.cash_balance += notional
    Transaction(type="TRADE_SELL", amount=+notional, reference_id=trade_id)

insert ProcessedEvent(event_id)
```
On **any** exception call `clear_processed(redis, "account", event_id)` before re-raising, or the broker retry will skip the handler.

**`order.cancelled`** and **`order.rejected`** → release the remaining reservation for `order_id` (idempotent, no-op if absent).

## 3.6 Edge cases — Account Service

1. **Double-spend across two browser tabs.** Two reservations for the same user land at once; the Redis lock serialises them and the second sees the reduced available balance → 409. **Log every acquire/release with the user id** — that log line is your demo evidence for the distributed-locking requirement.
2. **Redis down** → `distributed_lock` raises → **503. Fail closed.** Reserving without the lock creates money from nothing.
3. **Lock expiry mid-transaction.** TTL is 5 s; a slow DB could outlive it. That is why the row lock (`SELECT FOR UPDATE`) and the `CHECK` constraints exist — the Redis lock is for throughput, the DB is for correctness.
4. **Releasing a lock you no longer own** — handled by the Lua compare-and-delete in `common.redis_client`. Never `DEL` the key directly.
5. **Reservation replay** (Order Service retried after a timeout) → idempotent on `order_id`, no second hold.
6. **Release called twice** (once by `order.cancelled`, once by `order.rejected`) → the second returns `0.0000` with 200.
7. **Over-release** — always compute `remaining`, never assume; clamp at 0 and let the `CHECK` catch a genuine bug.
8. **Price improvement.** Buyer reserved at limit 102, filled at 100 → `consume = notional(100)`; the extra 2/share stays held until the order finishes, then is released. **Test this explicitly — it is the most commonly wrong path.**
9. **MARKET buy over-reservation.** Order Service reserves `qty × last_price × 1.05`; the final fill (`buy_order_remaining == 0`) releases everything unused.
10. **Partial fills** — one order produces several trade events; each consumes part of the hold; only the last releases the remainder.
11. **The seller has no funds reservation** — correct by design (sellers reserve *shares*, in Portfolio). Credit cash only; don't look for one.
12. **Self-trade** (`buyer_user_id == seller_user_id`) — the engine prevents it, but be defensive: acquire the lock **once** and process both legs inside it (re-acquiring the same key deadlocks).
13. **Duplicate `trade.executed`** → Redis idem + `ProcessedEvent` PK. Two layers, because Redis can be flushed.
14. **Deposit of `"0"`, `"-100"`, `"abc"`, `1e9`, `"10.00001"`** → all 400 with a clear message. Use `to_money` plus explicit range checks.
15. **Deposit concurrent with order placement** — both take `lock:funds:{user_id}` and `SELECT FOR UPDATE`, so they serialise.
16. **Float contamination** — if any endpoint returns `10000.0` instead of `"10000.0000"`, the UI will eventually show `10000.000000000002`. Serialise every `Decimal` with `money_str`.
17. **Account auto-creation race** — two concurrent first-touches insert the same PK → catch `IntegrityError`, rollback, re-select.
18. **Events for a user who never called `/balance`** → `get_or_create_account` inside the handler.
19. **`processed_events` growth** — fine for a demo; note in `SERVICE_NOTES.md` that production would prune > 7 days.
20. **Transaction listing** — paginate, `limit` capped at 200, index `(user_id, created_at DESC)`. A bot user generates thousands of rows.

---

# SECTION 4 — YOUR FRONTEND PAGES

All pages import from the pre-built shell. Money arrives as strings — use `money()` / `price()` from `lib/format`, never raw arithmetic on what you send back.

**`app/login/page.tsx`** — email-or-username + password, `useAuth().login()`, show `ApiError.detail` in an `<ErrorBox>`, honour `?next=` on success, link to register. Disable submit while in flight.

**`app/register/page.tsx`** — email, username, password, confirm, optional full name. Client validation mirroring §2.3 (username regex, password ≥ 8 chars with a letter and a digit), then `useAuth().register()`. On 409 show the backend `detail` ("email already registered").

**`app/wallet/page.tsx`** — `<Protected>`; balance card (cash / held / **available**, with a tooltip explaining held = reserved by open orders); `<DepositForm>` with quick amounts 1k / 10k / 100k, validated by `validateAmount()`; transaction table (type badge, signed amount coloured by sign, balance after, reference, time). Refresh balance after a successful deposit and `useToast().push("success", ...)`.

**`app/page.tsx`** (dashboard) — `<Protected>`; account summary row (available cash, portfolio value from `PortfolioAPI.pnl()`, total P&L coloured); `<SymbolTable>` listing all 8 symbols from `MarketAPI.symbols()` with live prices from `useLivePrices(symbols)`, 24 h change coloured, flash animation on tick (`flash-up` / `flash-down` classes are already in `globals.css`), each row linking to `/market/{symbol}`; a "Live / Reconnecting" indicator from the `status` returned by `useLivePrices`.

Edge cases: 401 is already handled centrally in `lib/api.ts`; guard `localStorage` with `typeof window !== "undefined"` (the shell already does); any component using live data must be `'use client'` and start from a `<Spinner>`; portfolio value may be briefly stale (CQRS) — show "updating…" rather than a wrong number; empty states use `<Empty>`.

---

# SECTION 5 — TWO SHARED TOOLS YOU ALSO OWN

## 5.1 `scripts/smoke_test.py` — the team's merge gate and demo script

Pure `httpx`, no pytest, prints ✅/❌ per step, exits non-zero on failure. Team A runs it at every merge window.

1. `GET /health` on the gateway → 200.
2. Register user A and user B (random emails) → 201 + token.
3. Duplicate register for A → **409**.
4. Login with the wrong password → **401**.
5. `GET /api/users/me` with no token → **401**.
6. Deposit 100000 for A and B → balance reflects it.
7. Deposit `-5` → **400**.
8. `GET /api/market/symbols` → 8 symbols; `GET /api/market/candles/AAPL?interval=1m&limit=50` → non-empty.
9. B sells shares it does not own → **409 insufficient shares**.
10. Give B shares first: B buys 10 AAPL from a seeded bot (or the market maker), poll until `FILLED`.
11. B places `SELL LIMIT AAPL 10 @ 200.00`; A places `BUY LIMIT AAPL 10 @ 200.00`.
12. Poll `GET /api/orders/{id}` up to 10 s → both reach `FILLED`.
13. `GET /api/portfolio` for A → 10 AAPL @ avg 200.
14. `GET /api/account/balance` for A → cash reduced by 2000, held 0.
15. `GET /api/notifications` for A → at least one fill notification.
16. `GET /api/market/quote/AAPL` → 200; `GET /api/book/AAPL` → both orders gone.
17. A places `BUY LIMIT AAPL 5 @ 1.00` (won't fill) → `DELETE` → `CANCELLED`, held back to 0.
18. `DELETE` the same order again → **409**.
19. `quantity: 0` → 400; `price: 100.005` → 400; `symbol:"FAKE"` → 400.
20. WebSocket `ws://localhost:8000/ws/market?symbols=AAPL`, trigger a trade, assert a tick arrives within 5 s.

## 5.2 `infra/seed/market_maker.py` — makes the demo alive

Uses the **public API through the gateway**, so it doubles as an end-to-end integration test.
* Register + login 4 bot users (`bot1@mse.local` … `bot4@mse.local`, password `BotPassw0rd!`) and one demo human `demo@mse.local` / `DemoPassw0rd!`.
* Deposit 1,000,000 for each bot, 100,000 for the demo user.
* Give bots inventory first (they must own shares before they can quote asks) — the simplest way is bot1 buys from bot2 at the seed price via crossing limit orders, then both quote.
* For each symbol quote 5 bids from `last × 0.999` downward and 5 asks from `last × 1.001` upward in 0.1 % steps, 50 shares each.
* `--loop`: re-quote every 30 s and occasionally cross the spread so trades print, candles move and notifications fire live during the presentation.
* Idempotent: re-running must not double-register (catch 409 and log in instead).
* **Rate limits**: `POST /api/orders` is 30/min. Throttle the bot (`asyncio.sleep`) or ask Team A to exempt `@mse.local` users — coordinate this, don't guess.

---

# SECTION 6 — LOCAL DEV & TESTING ALONE

```yaml
# services/account-service/docker-compose.dev.yml
services:
  postgres: { image: postgres:16-alpine, environment: {POSTGRES_USER: mse, POSTGRES_PASSWORD: mse_pw, POSTGRES_DB: account_db}, ports: ["5434:5432"] }
  redis:    { image: redis:7-alpine, ports: ["6379:6379"] }
  rabbitmq: { image: rabbitmq:3.13-management-alpine, ports: ["5672:5672","15672:15672"] }
```
PowerShell: `$env:PYTHONPATH=".;../../libs"; uvicorn app.main:app --reload --port 8002`

Fake the Matching Engine — `services/account-service/scripts/emit_event.py`:
```python
# usage: python emit_event.py trade.executed '{"trade_id":"...", ...}'
import asyncio, json, sys
from common.events import Broker
async def main():
    b = Broker("amqp://guest:guest@localhost:5672/", "tester")
    await b.connect(); await b.publish_event(sys.argv[1], json.loads(sys.argv[2])); await b.close()
asyncio.run(main())
```

**`selfcheck.py` — must produce exactly these numbers before you push:**

*User Service:* register → 201 + token; register the same email uppercased → 409; login by username and by email → both work; wrong password → 401; `/users/me` with the token → same id; 100-byte password → 400; username `ab` → 400.

*Account Service:* deposit 10000 → available 10000; reserve 2000 (order X) → available 8000, held 2000; reserve order X again → **still** held 2000; reserve 9000 → 409; emit `trade.executed` filling 5 @ 200 with `buy_order_limit_price` 210 and `buy_order_remaining` 5 → cash 9000, held 950; emit the final fill with `buy_order_remaining` 0 → cash 8000, held 0; emit the same event twice → numbers unchanged; release a cancelled order twice → second returns `"0.0000"`; deposit `-1` → 400.

---

# SECTION 7 — HOUR-BY-HOUR PLAN (24 h)

| Hours | Work |
|---|---|
| 0–1 | Pull repo, read `libs/common` end to end, start dev containers. |
| 1–4 | **User Service complete** — models, alembic, register/login/me, bcrypt, all edge cases §2.4. Swagger green. |
| 4–5 | User `selfcheck.py` + Dockerfile + entrypoint. **Push `feat/team-b-identity`.** |
| 5–11 | **Account Service complete** — models + constraints, deposit/balance/transactions, reservations with the Redis lock, event handlers, idempotency. |
| 11–12 | Account `selfcheck.py` passing with the exact numbers in §6. **Push.** |
| 12–15 | Frontend: `/login`, `/register`, `/wallet`, dashboard `/` + `SymbolTable` + `DepositForm`. Build against Team D's `frontend/mock-api.py` if the backend isn't merged yet. |
| 15–17 | `scripts/smoke_test.py` written in full (against the contract — it does not need a running system to be written). |
| 17–18 | `infra/seed/market_maker.py`. **Push — Team A's merge window.** |
| 18–21 | On call for integration: Team C hits your `/internal/reservations` for real. Fix fast, don't refactor. |
| 21–23 | `SERVICE_NOTES.md` ×2. Screenshots: Swagger, a `lock acquired` / `lock released` log pair, the transactions table after a fill. |
| 23–24 | Freeze. No new code. |

---

# SECTION 8 — DEFINITION OF DONE

- [ ] Both services build from the repo root; `/health`, `/ready`, `/docs` green.
- [ ] Alembic creates every table on an empty database.
- [ ] Both `selfcheck.py` scripts pass with the exact numbers in §6.
- [ ] Every money value in every response is a 4-dp **string**.
- [ ] `insufficient buying power` (409) provably works, and the funds are released afterwards.
- [ ] Replaying any event twice changes nothing.
- [ ] Redis down → reservations return 503, not 500, and no funds move.
- [ ] Logs show `lock acquired` / `lock released` around every funds mutation.
- [ ] Login, register, wallet and dashboard pages work end to end with live prices.
- [ ] `smoke_test.py` and `market_maker.py` run against the merged system.
- [ ] `SERVICE_NOTES.md` written for both services.
