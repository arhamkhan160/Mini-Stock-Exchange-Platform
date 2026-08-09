# TEAM D — Market Data Service · Portfolio Service · Charts & Portfolio UI

**Owner: Mustain Billah Taj (220042166)**
**Your services: Market Data (8005) and Portfolio (8006) — live prices, candlestick history with master–slave replication, holdings and P&L.**

> Give this whole file to Claude Code in the repo root and say:
> *"Read TEAM_D_MARKETDATA_AND_FRONTEND.md and implement my sections completely. `libs/common/` and the `frontend/` shell already exist — read them, import from them, do not rewrite them."*

**You are blocked by nobody.** Market Data and Portfolio only consume `trade.executed` (fake it with the script in §6). You also build `frontend/mock-api.py` in hour 1 — the mock gateway the whole team uses to build UI before the backend exists.

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
| `services/user-service/**`, `services/account-service/**` | B |
| `frontend/app/{login,register,wallet}/page.tsx`, `frontend/app/page.tsx`, `frontend/components/{SymbolTable,DepositForm}.tsx` | B |
| `scripts/smoke_test.py`, `infra/seed/market_maker.py` | B |
| `services/order-service/**`, `services/matching-engine/**` | C |
| `frontend/app/orders/page.tsx`, `frontend/components/{OrderTicket,OrderBook,RecentTrades,OrdersTable}.tsx` | C |
| **`services/market-data-service/**`, `services/portfolio-service/**`** | **D** |
| **`frontend/app/market/[symbol]/page.tsx`, `frontend/app/portfolio/page.tsx`, `frontend/components/{CandleChart,HoldingsTable}.tsx`** | **D** |
| **`infra/seed/seed_market_data.py`, `frontend/mock-api.py`** | **D** |

## 1.3 Ports

| Component | Container | Port | Host |
|---|---|---|---|
| Frontend | `frontend` | 3000 | 3000 |
| API Gateway | `gateway` | 8000 | 8000 |
| User | `user-service` | 8001 | 8001 |
| Account | `account-service` | 8002 | 8002 |
| Order | `order-service` | 8003 | 8003 |
| Matching Engine | `matching-engine` | 8004 | 8004 |
| **Market Data** | `market-data-service` | **8005** | 8005 |
| **Portfolio** | `portfolio-service` | **8006** | 8006 |
| Notification | `notification-service` | 8007 | 8007 |
| Postgres user / account / order | `postgres-user` / `-account` / `-order` | 5432 | 5433 / 5434 / 5435 |
| **Postgres market PRIMARY / REPLICA** | `postgres-market-primary` / `-replica` | 5432 | **5436 / 5437** |
| **Postgres portfolio** / notification | `postgres-portfolio` / `-notification` | 5432 | **5438** / 5439 |
| Redis | `redis` | 6379 | 6379 |
| RabbitMQ | `rabbitmq` | 5672 / 15672 | 5672 / 15672 |

## 1.4 Money & quantity rules

* Money is `Decimal`, 4 dp, `ROUND_HALF_UP`. **Never float.** Postgres `NUMERIC(18,4)`.
* Money crosses the wire as a **JSON string**: `"195.5000"`.
* **The one documented exception is yours**: `/market/candles` returns `open/high/low/close` as **numbers** and `time` as an **epoch-seconds int**, because `lightweight-charts` requires them. Write this in your `SERVICE_NOTES.md` — it is a deliberate deviation, not sloppiness.
* Quantity is a positive **int**. Postgres `BIGINT`.
* Use `common.money`: `to_money`, `money_str`, `validate_price`, `validate_quantity`, `notional`.
* Symbols: `common.symbols.SYMBOLS`, `SEED_PRICES`, `normalize_symbol`.

## 1.5 Auth

JWT HS256, secret `JWT_SECRET` identical in every container. Claims: `sub`, `email`, `username`, `iat`, `exp`, `type:"access"`. Expiry 1440 min.
Gateway validates and forwards `Authorization` unchanged; every service validates again via `common.security.get_current_user`.
**Every Market Data read endpoint is public** (browsing prices needs no login — the gateway whitelists `/api/market/`). **Portfolio endpoints all require JWT.**
`/internal/*` requires `X-Internal-Key` (`Depends(require_internal_key)`); the gateway refuses to proxy `/internal/`.

## 1.6 Gateway routing table

Strip `/api`, keep the rest of the path and the query string.

| Method | Public path | Upstream | Auth |
|---|---|---|---|
| POST | `/api/auth/register`, `/api/auth/login`, `/api/auth/login-json` | user-service | public |
| GET/PATCH | `/api/users/me` | user-service | JWT |
| GET | `/api/account/balance`, `/api/account/transactions` | account-service | JWT |
| POST | `/api/account/deposit` | account-service | JWT |
| POST | `/api/orders` | order-service | JWT, 30/min |
| GET | `/api/orders`, `/api/orders/{id}` | order-service | JWT |
| DELETE | `/api/orders/{id}` | order-service | JWT |
| GET | `/api/book/{symbol}` | matching-engine | public |
| GET | `/api/market/symbols`, `/quote/{s}`, `/candles/{s}`, `/trades/{s}` | market-data-service | public |
| GET | `/api/portfolio`, `/api/portfolio/pnl` | portfolio-service | JWT |
| GET/POST | `/api/notifications*` | notification-service | JWT |
| WS | `/ws/market?symbols=AAPL,TSLA` | market-data-service `/ws/market` | public |

Errors everywhere: `{"detail": "message"}` — 400 validation, 401 auth, 403 not yours, 404 missing, 409 business conflict, 429 rate limited, 503/504 upstream.

## 1.7 Event contract

Exchange `exchange.events` (topic, durable). Dead-letter `exchange.events.dead`. Envelope:
```json
{ "event_id":"<uuid4>", "event_type":"trade.executed",
  "occurred_at":"2026-08-08T12:00:00.000000Z", "version":1, "payload":{ } }
```

| Routing key | Published by | **You consume?** |
|---|---|---|
| `order.accepted` | Order (C) | no |
| `order.rejected` | Order (C) | **yes — Portfolio releases the share reservation** |
| `order.cancel_requested` | Order (C) | no |
| `order.cancelled` | Matching Engine (C) | **yes — Portfolio releases the share reservation** |
| `order.cancel_rejected` | Matching Engine (C) | no |
| `trade.executed` | Matching Engine (C) | **yes — Market Data (candles/ticks) AND Portfolio (holdings)** |

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
Your queues: `q.marketdata.trade_executed`, `q.portfolio.trade_executed`, `q.portfolio.order_cancelled`, `q.portfolio.order_rejected`.

## 1.8 Redis keys — **you own the writes to the market ones**
```
md:last_price:{SYMBOL}     "195.5000"     <- Portfolio reads it for unrealized P&L; Order reads it for MARKET orders
md:quote:{SYMBOL}          {"price","quantity","ts"}
CHANNEL md:ticks           {"symbol","price","quantity","ts"}
lock:shares:{user_id}:{SYMBOL}  Portfolio's distributed lock
idem:{service}:{event_id}  SET NX EX 86400 — event idempotency
```
**If you stop writing `md:last_price`, Portfolio's unrealized P&L silently goes to zero and the Order Service rejects every MARKET order.** That key is your contract with two other services.

## 1.9 Internal service-to-service REST
```
POST portfolio-service:8006 /internal/share-reservations                <- YOU IMPLEMENT
     {"order_id","user_id","symbol","quantity":10}
     201 {"reservation_id","order_id","quantity","status":"HELD"}
     409 {"detail":"insufficient shares"}      Idempotent on order_id.
POST portfolio-service:8006 /internal/share-reservations/{order_id}/release  -> 200 {"released":10}
                                                                        <- YOU IMPLEMENT
POST account-service:8002 /internal/reservations  (B implements)
GET  order-service:8003 /internal/orders/open     (C implements)
GET  user-service:8001  /internal/users/{id}      (B implements)
```

## 1.10 Every service exposes
`GET /health` → `{"status":"ok","service":"<name>"}`, no DB call. `GET /ready` → DB (+ replica) + Redis + broker, 503 if any down. Swagger `/docs`, `version="1.0.0"`.

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
`requirements.txt` = `-r ../../requirements-base.txt`. `entrypoint.sh` — **LF only**; migrations always run against the **primary**:
```bash
#!/bin/sh
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${SERVICE_PORT}"
```
Alembic `env.py` uses `sync_url(settings.DATABASE_URL)` from `common.db`. **Hand-write** `versions/0001_initial.py`.

## 1.12 Frontend — the shell is already built

Frozen and ready: `lib/api.ts` (`MarketAPI.candles/quote/symbols/trades/book`, `PortfolioAPI.get/pnl`, `OrderAPI.*`), `lib/auth.tsx`, `lib/ws.ts` (`useLivePrices()`, `subscribeMarket()` with backoff reconnect), `lib/format.ts`, `lib/types.ts` (`Candle`, `Holding`, `Portfolio`, `Tick` …), `components/ui.tsx` (`Card`, `Button`, `Input`, `Select`, `Badge`, `Tone`, `Table`, `Empty`, `Spinner`, `ErrorBox`), `Toast.tsx`, `Protected.tsx`, `Navbar.tsx`, dark theme tokens, and the `flash-up` / `flash-down` CSS animations.

**Use these primitives — do not invent new button/card styles.**

| Route / component | Owner |
|---|---|
| `app/page.tsx`, `app/login`, `app/register`, `app/wallet`, `components/{SymbolTable,DepositForm}.tsx` | B |
| `app/orders/page.tsx`, `components/{OrderTicket,OrderBook,RecentTrades,OrdersTable}.tsx` | C |
| **`app/market/[symbol]/page.tsx`, `app/portfolio/page.tsx`, `components/{CandleChart,HoldingsTable}.tsx`** | **D** |
| `app/notifications/page.tsx`, `components/NotificationBell.tsx` | A |

`OrderTicket`, `OrderBook` and `RecentTrades` **already exist as compiling stubs**, so your market page builds from hour 0 and Team C swaps in the real bodies later. Import them exactly as:
```tsx
<OrderTicket  symbol={symbol} lastPrice={lastPrice} onPlaced={refresh} />
<OrderBook    symbol={symbol} onPriceClick={(p) => setTicketPrice(p)} />
<RecentTrades symbol={symbol} />
```

## 1.13 Merge protocol
* Your branch: **`feat/team-d-market-portfolio`**. A works on `main`.
* Nobody edits `libs/common`, the frontend shell, `docker-compose.yml`, `.env`, or another team's folder.
* Push at least every 3 hours even if incomplete.
* Deliver in your folders: `Dockerfile`, `requirements.txt`, `entrypoint.sh`, `alembic/`, `selfcheck.py`, `SERVICE_NOTES.md`.
* Merge windows: **hour 13** and **hour 18**.

---

# SECTION 2 — YOUR SERVICE 1: MARKET DATA (port 8005)

**The master–slave split is a graded requirement of the proposal.** Team A makes streaming replication run; **you** must actually route reads to the replica and writes to the primary.

## 2.1 Two engines — the read/write split
```python
from common.config import settings
from common.db import make_engine, make_sessionmaker

write_engine = make_engine(settings.DATABASE_URL)                        # PRIMARY
replica_url  = settings.DATABASE_REPLICA_URL or settings.DATABASE_URL    # graceful fallback
read_engine  = make_engine(replica_url)
WriteSession, ReadSession = make_sessionmaker(write_engine), make_sessionmaker(read_engine)
```
* **Every write** (trade insert, candle upsert, symbol seed, Alembic) → `WriteSession` / `DATABASE_URL`.
* **Every chart/history read** (`/market/candles`, `/market/trades`, `/market/symbols`) → `ReadSession`.
* At startup log which URL each engine got **and** the result of `SELECT pg_is_in_recovery()` on the read engine — `true` proves you are really on the replica. **Screenshot that log line for the report.**
* If the replica is unreachable at request time, catch `OperationalError`, log a warning, retry once on `WriteSession`. Never 500 a chart because a replica lagged.

⚠️ Writing through the read session raises `cannot execute INSERT in a read-only transaction`. If you see that, a write path is on the wrong session.

## 2.2 Models
```python
class Symbol(Base):
    __tablename__ = "symbols"
    symbol = String(10) pk; name = String(120); seed_price = Numeric(18,4); created_at

class Trade(Base):
    __tablename__ = "trades"
    id = uuid pk
    trade_id       = UUID unique not null       # idempotency lives here
    symbol         = String(10) not null
    price          = Numeric(18,4) not null
    quantity       = BigInteger not null
    aggressor_side = String(4)
    executed_at    = DateTime(timezone=True) not null
    __table_args__ = (Index("ix_trades_symbol_time", "symbol", "executed_at"),)

class Candle(Base):
    __tablename__ = "candles"
    id = uuid pk
    symbol = String(10) not null; interval = String(4) not null      # '1m' | '5m'
    bucket_start = DateTime(timezone=True) not null
    open = high = low = close = Numeric(18,4) not null
    volume = BigInteger default 0; trade_count = Integer default 0
    __table_args__ = (
        UniqueConstraint("symbol","interval","bucket_start", name="uq_candle_bucket"),
        Index("ix_candles_lookup", "symbol", "interval", "bucket_start"),
    )
```
Seed the 8 `symbols` rows from `common.symbols` on startup if the table is empty (`ON CONFLICT DO NOTHING`).

## 2.3 The `trade.executed` handler (write path — primary only)
```python
async def handle_trade(env):
    if await seen_event(redis, "marketdata", env["event_id"]): return   # read-only
    try:
        p     = env["payload"]
        sym   = normalize_symbol(p["symbol"])
        price = to_money(p["price"]); qty = int(p["quantity"])
        ts    = datetime.fromisoformat(p["executed_at"].replace("Z", "+00:00"))
        async with WriteSession() as s, s.begin():
            await s.execute(pg_insert(Trade).values(...).on_conflict_do_nothing(index_elements=["trade_id"]))
            for interval, minutes in (("1m", 1), ("5m", 5)):
                await upsert_candle(s, sym, interval, floor_bucket(ts, minutes), price, qty)
        await redis.set(f"md:last_price:{sym}", money_str(price))
        await redis.set(f"md:quote:{sym}", json.dumps({"price": money_str(price), "quantity": qty,
                                                        "ts": ts.isoformat()}))
        await redis.publish("md:ticks", json.dumps({"symbol": sym, "price": money_str(price),
                                                     "quantity": qty, "ts": int(ts.timestamp())}))
    except Exception:
        raise   # the processed_events PK is the authority; see the idempotency rule below
```

`floor_bucket(ts, minutes)` — **always UTC**:
```python
ts = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
return ts.replace(minute=(ts.minute // minutes) * minutes)
```

`upsert_candle` — one atomic statement, no read-modify-write race:
```python
from sqlalchemy.dialects.postgresql import insert as pg_insert
stmt = pg_insert(Candle).values(id=uuid4(), symbol=sym, interval=interval, bucket_start=bucket,
                                open=price, high=price, low=price, close=price,
                                volume=qty, trade_count=1)
await s.execute(stmt.on_conflict_do_update(
    index_elements=["symbol", "interval", "bucket_start"],
    set_={"high":  func.greatest(Candle.high, stmt.excluded.high),
          "low":   func.least(Candle.low,  stmt.excluded.low),
          "close": stmt.excluded.close,
          "volume": Candle.volume + stmt.excluded.volume,
          "trade_count": Candle.trade_count + 1}))
```
**`open` is only ever set on INSERT** — that is exactly what `on_conflict_do_update` gives you, because `open` is absent from `set_`.
**Build 5m candles from trades directly, never by re-aggregating 1m candles** — deriving them double-counts on any replay and gets the open wrong.

## 2.4 REST endpoints (all reads from the **replica**)

| Method | Path | Notes |
|---|---|---|
| GET | `/market/symbols` | 8 rows `{symbol,name,last_price,change,change_pct,volume_24h}`; `last_price` via one Redis `MGET`; `change` vs the close 24 h ago |
| GET | `/market/quote/{symbol}` | Redis `md:quote` first, then newest trade from the replica, then `SEED_PRICES` with `"stale": true` |
| GET | `/market/candles/{symbol}?interval=1m\|5m&limit=300&from=&to=` | **chart data**, `limit` ≤ 1000, ascending, `time` = epoch **seconds** int, OHLC as **numbers** |
| GET | `/market/trades/{symbol}?limit=50` | newest first, prices as strings |
| GET | `/market/stats` | one-shot market summary |
| GET | `/internal/replication-status` | `{"is_replica",​"replica_url_configured","lag_bytes"}` — your proof for the report |
| WS | `/ws/market?symbols=AAPL,TSLA` | live ticks |

**Gap filling on candle reads.** A minute with no trades has no row, and a chart with holes looks broken. After fetching, forward-fill missing buckets between first and last with `{open=high=low=close=previous close, volume:0}`. Cap the fill at `limit` buckets so a 2-day gap can't generate a million objects.

## 2.5 WebSocket `/ws/market`
```python
@app.websocket("/ws/market")
async def ws_market(ws: WebSocket):
    await ws.accept()
    symbols = {normalize_symbol(s) for s in ws.query_params.get("symbols","").split(",") if s.strip()} or set(SYMBOLS)
    conn = Connection(ws, symbols); MANAGER.add(conn)
    try:
        for sym in symbols:                       # immediate snapshot so the UI is never blank
            p = await redis.get(f"md:last_price:{sym}")
            if p: await ws.send_json({"type":"tick","symbol":sym,"price":p,"quantity":0,"ts":now_epoch()})
        while True:
            msg = await ws.receive_json()         # {"action":"subscribe"|"unsubscribe","symbols":[...]}
            ...update conn.symbols...
    except WebSocketDisconnect:
        pass
    finally:
        MANAGER.remove(conn)
```
**One** background task per process subscribes to Redis and fans out to local connections — this is the Pub/Sub fan-out the proposal asks for, and it is what would let you run several Market Data replicas behind the gateway:
```python
async def tick_pump():
    while True:                                   # survive a Redis restart
        try:
            pubsub = redis.pubsub(); await pubsub.subscribe("md:ticks")
            async for m in pubsub.listen():
                if m["type"] != "message": continue
                tick = json.loads(m["data"])
                await MANAGER.broadcast(tick["symbol"], {"type": "tick", **tick})
        except Exception:
            log.exception("tick pump died, retrying"); await asyncio.sleep(2)
```
Start it in the lifespan with `asyncio.create_task`, cancel on shutdown.
`MANAGER.broadcast` must iterate a **copy** of the connection set, wrap each `send_json` in `try/except`, and drop any connection that raises — one dead browser tab must never stop the fan-out. Send `{"type":"ping"}` every 20 s so proxies don't kill idle sockets.

## 2.6 Edge cases — Market Data

1. **Writing to the replica** → `cannot execute INSERT in a read-only transaction`. Every write goes through `WriteSession`.
2. **Replica lag** — a candle written 200 ms ago may not be readable yet. The frontend also applies the live WS tick, so the user sees no lag. Document it as intended CQRS behaviour.
3. **Replica down** → catch `OperationalError`, log, retry once on the primary. Never 500 a chart.
4. **`DATABASE_REPLICA_URL` unset** → falls back to the primary and everything still works. This is your insurance if replication isn't ready by hour 18.
5. **Duplicate trade events** → `trade_id` unique + `ON CONFLICT DO NOTHING` + Redis idem. Without it a redelivery double-counts volume.
6. **Concurrent candle upserts** for one bucket → the single `ON CONFLICT DO UPDATE` statement. A read-modify-write loses trades under load.
7. **`open` overwritten on update** — the single most common candle bug. `open` must be absent from `set_`.
8. **Timezones.** Everything UTC. `datetime.fromisoformat` on `"...Z"` needs `.replace("Z","+00:00")`. A naive datetime in `bucket_start` silently shifts the whole chart.
9. **`time` must be epoch seconds** — lightweight-charts renders nothing for ISO strings or millisecond timestamps.
10. **Duplicate or unsorted `time`** → `Assertion failed: data must be asc ordered by time`. Sort ascending and de-duplicate before returning.
11. **Empty candle set** → return `[]`; the UI shows "No data yet".
12. **Unknown symbol** → 404 `{"detail":"unknown symbol"}`. Validate with `normalize_symbol` before any query.
13. **`?limit=999999`** → clamp to 1000.
14. **WS client disconnects mid-broadcast** → per-connection try/except, drop, continue.
15. **Slow WS client** → don't buffer forever; drop on send failure.
16. **WS with no `symbols` param** → subscribe to all 8, don't crash on the empty string.
17. **Redis restart wipes `md:last_price`** → warm the cache on startup from the newest trade per symbol on the replica, falling back to `SEED_PRICES`. **Portfolio and Order Service both depend on this key existing.**
18. **`pubsub.listen()` dies** when Redis restarts → the `while True` + backoff above.
19. **Float in the tick payload** → prices stay strings on the wire; the frontend does `Number()` for display only.
20. **24 h change with no history** → `change:"0.0000"`, `change_pct: 0.0`. Never `null`, never divide by zero.

---

# SECTION 3 — YOUR SERVICE 2: PORTFOLIO (port 8006, `portfolio_db`)

The proposal's CQRS read model: holdings and P&L are a **projection of executed trades**, eventually consistent. It also owns **share reservations** for SELL orders — the symmetric counterpart to Account's funds reservations. Without it, users can sell shares they don't own.

## 3.1 Models
```python
class Holding(Base):
    __tablename__ = "holdings"
    id = uuid pk
    user_id           = UUID index not null
    symbol            = String(10) not null
    quantity          = BigInteger not null default 0
    reserved_quantity = BigInteger not null default 0
    avg_cost          = Numeric(18,4) not null default 0
    realized_pnl      = Numeric(18,4) not null default 0
    updated_at
    __table_args__ = (
        UniqueConstraint("user_id","symbol", name="uq_user_symbol"),
        CheckConstraint("quantity >= 0", name="ck_qty_non_negative"),
        CheckConstraint("reserved_quantity >= 0 AND reserved_quantity <= quantity", name="ck_reserved_bounds"),
    )

class ShareReservation(Base):
    __tablename__ = "share_reservations"
    order_id = UUID pk; user_id = UUID index; symbol = String(10)
    quantity = BigInteger; consumed = BigInteger default 0; released = BigInteger default 0
    status = String(16) default "HELD"; created_at / updated_at

class TradeHistory(Base):
    __tablename__ = "trade_history"
    id = uuid pk; trade_id = UUID; user_id = UUID index; symbol = String(10); side = String(4)
    quantity = BigInteger; price = Numeric(18,4); realized_pnl = Numeric(18,4) default 0; executed_at
    __table_args__ = (UniqueConstraint("trade_id","user_id", name="uq_trade_user"),)

class ProcessedEvent(Base):
    event_id = UUID pk; event_type = String(64); handled_at
```

## 3.2 Accounting rules — the grader will check these numbers

**BUY fill** — weighted average cost:
```
new_qty = qty + fill_qty
new_avg = to_money((qty * avg_cost + fill_qty * fill_price) / new_qty)
qty, avg_cost = new_qty, new_avg        # realized_pnl unchanged
```
**SELL fill** — realize against average cost; **average cost does not change**:
```
realized          += to_money((fill_price - avg_cost) * fill_qty)
qty               -= fill_qty
reserved_quantity -= min(reserved_quantity, fill_qty)
if qty == 0: avg_cost = 0
```
**Unrealized P&L** (computed at read time):
```
mark = redis GET md:last_price:{SYMBOL}     -> missing? mark = avg_cost, price_stale = true
market_value = mark * quantity
unrealized   = (mark - avg_cost) * quantity
```
`available_quantity = quantity - reserved_quantity` — that is what a SELL order may use.

## 3.3 Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/portfolio` | JWT → `{user_id, holdings:[{symbol,name,quantity,available_quantity,reserved_quantity,avg_cost,last_price,market_value,unrealized_pnl,realized_pnl,price_stale}], totals:{…}}` |
| GET | `/portfolio/pnl` | JWT → `{total_market_value,total_cost_basis,total_unrealized_pnl,total_realized_pnl,as_of}` |
| GET | `/portfolio/trades?limit=50&offset=0&symbol=` | JWT, newest first |
| GET | `/portfolio/holdings/{symbol}` | JWT, 404 if never held |
| POST | `/internal/share-reservations` | internal key, idempotent on `order_id` |
| POST | `/internal/share-reservations/{order_id}/release` | internal key, idempotent |
| GET | `/internal/holdings/{user_id}` | internal key, debugging |

## 3.4 Event handlers
```
trade.executed:
  if await seen_event(redis,"portfolio",event_id): return   # read-only check
  lock lock:shares:{buyer_user_id}:{symbol}  -> apply BUY fill (create the holding if absent)
  lock lock:shares:{seller_user_id}:{symbol} -> apply SELL fill, consume the share reservation
                                                for sell_order_id, release the remainder when
                                                sell_order_remaining == 0
  insert TradeHistory for BOTH users
  insert ProcessedEvent(event_id)

order.cancelled / order.rejected:
  release the share reservation for order_id if one exists (BUY orders have none -> 200, released 0)
```
Insert `ProcessedEvent(event_id)` inside the SAME transaction as the holdings updates, and call `mark_event_processed(redis, "portfolio", event_id)` only AFTER the commit.

**Idempotency is DATABASE-FIRST — Redis is only a cache.** The authority is the
`processed_events` primary key, inserted in the SAME transaction as the work.
The Redis marker is READ before the work and WRITTEN only after the commit:

```python
if await seen_event(redis, "portfolio", env["event_id"]):   # read-only check
    return

async with SessionLocal() as session:
    ...apply the event...
    session.add(ProcessedEvent(event_id=uuid.UUID(env["event_id"])))
    try:
        await session.commit()
    except IntegrityError:            # another delivery won the race
        await session.rollback()
        return

await mark_event_processed(redis, "portfolio", env["event_id"])   # AFTER commit
```

Never `SET NX` before the work: a crash between the claim and the commit leaves a
marker for work that never happened, the redelivery sees "already processed",
acks, and the event is silently lost. A read-only check can only cause a
redundant retry, which the primary key stops. Prefer a duplicate over a loss.

A raising handler does not block its queue — `common.events.Broker` sends the
message to `<queue>.retry` (5 s TTL, dead-letters back to the source queue) and
gives up to `exchange.events.dead` after 5 attempts.


## 3.5 Edge cases — Portfolio

1. **Selling shares you don't own.** `/internal/share-reservations` must check `quantity - reserved_quantity >= requested` → else **409 `insufficient shares`**. Without this the exchange can be shorted infinitely.
2. **Selling the same 10 shares in two orders** — `reserved_quantity` is exactly what prevents it.
3. **Reservation replay** (Order Service retry) → idempotent on `order_id`.
4. **Release twice** → releases 0, returns 200.
5. **Trade for a buyer with no holding row** → `get_or_create` with qty 0, avg 0, then apply.
6. **Position going to zero** → keep the row (history + realized P&L live there), set `avg_cost = 0`, `quantity = 0`. Never delete.
7. **Avg-cost rounding drift** → quantize after every recompute, or trailing garbage accumulates over many fills.
8. **Divide by zero** when `new_qty == 0` in the BUY branch — impossible (`fill_qty > 0`), guard anyway.
9. **Missing mark price** → `mark = avg_cost`, `unrealized = 0`, `price_stale: true`. Never crash, never return `null`.
10. **`md:last_price` is a string** → `Decimal(value)`, never `float(...)`.
11. **Duplicate trade event** → Redis idem + the `uq_trade_user` constraint.
12. **Self-trade** (same user both legs) → acquire the lock **once** and apply BUY then SELL inside it; re-acquiring the same key deadlocks.
13. **Eventual consistency window** — the portfolio may lag a fill by a few hundred ms. That is the designed CQRS behaviour; document it and refetch ~1 s after a fill in the UI.
14. **Partial sell fills** consume the reservation incrementally; only `sell_order_remaining == 0` releases the remainder.
15. **Negative quantity must be impossible** — the DB `CHECK`s abort the transaction and the message retries rather than corrupting the book.
16. **A BUY order's cancel** hits `/internal/share-reservations/{id}/release` with no reservation → 200, `released: 0`. Do **not** 404; Order Service releases both sides blindly.
17. **Symbol case** → always `normalize_symbol` before storing or looking up.
18. **Large portfolios** — `GET /portfolio` would do N Redis GETs; use one `MGET` for all symbols.

---

# SECTION 4 — YOUR FRONTEND WORK

## 4.1 `components/CandleChart.tsx` — the demo centrepiece
```tsx
'use client';
import { createChart, ColorType, IChartApi, ISeriesApi } from 'lightweight-charts';

export default function CandleChart({ symbol, interval }: { symbol: string; interval: "1m" | "5m" }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>(); const series = useRef<ISeriesApi<'Candlestick'>>();
  useEffect(() => {
    if (!ref.current) return;
    chart.current = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: '#11151f' }, textColor: '#8b93a7' },
      grid: { vertLines: { color: '#1f2637' }, horzLines: { color: '#1f2637' } },
      timeScale: { timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    series.current = chart.current.addCandlestickSeries({     // v4 API
      upColor: '#26a69a', downColor: '#ef5350',
      wickUpColor: '#26a69a', wickDownColor: '#ef5350', borderVisible: false,
    });
    return () => { chart.current?.remove(); chart.current = undefined; };
  }, []);
  // history -> series.current.setData(await MarketAPI.candles(symbol, interval, 300))
  // live tick -> series.current.update({ time: bucket, open, high: max, low: min, close: price })
  return <div ref={ref} className="h-[420px] w-full" />;
}
```
⚠️ **`lightweight-charts` is pinned to 4.2.0.** v5 removed `addCandlestickSeries()` in favour of `addSeries(CandlestickSeries, …)`; every tutorial and most model output is v4. Mixing them gives `addCandlestickSeries is not a function`. **Do not bump the version.**
Import the chart with `dynamic(() => import('@/components/CandleChart'), { ssr: false })` — it touches `window`.

## 4.2 `app/market/[symbol]/page.tsx`
`'use client'`. Uppercase the URL symbol and 404 gracefully if it isn't one of the 8.
Layout: header (symbol, name, live price, 24 h change coloured, Live/Reconnecting indicator from `useLivePrices`) · `1m`/`5m` toggle · `<CandleChart>` · right column `<OrderTicket>` (Team C's, stub for now) · below `<OrderBook>` and `<RecentTrades>` (Team C's).
Wire `OrderBook`'s `onPriceClick` into the ticket price, and pass `onPlaced` to refetch.
Live tick handling: update the current bucket's candle with `series.update`, not `setData` — `setData` resets the viewport on every tick and makes the chart unusable.

## 4.3 `app/portfolio/page.tsx` + `components/HoldingsTable.tsx`
`<Protected>`. Summary cards: total market value, cost basis, unrealized P&L, realized P&L (coloured with `Tone`/`toneOf`). `<HoldingsTable>`: symbol (link to the market page), quantity, available, avg cost, last price, market value, unrealized, realized. Show a "prices may be delayed" note when any `price_stale` is true. `<Empty>` when there are no holdings ("You don't own anything yet — place your first order").
Refetch ~1 s after a fill notification (CQRS lag) and show "updating…" rather than a stale number.

## 4.4 Frontend edge cases
1. **`localStorage` on the server** — guard with `typeof window !== 'undefined'`; the shell already does. Anything with live data is `'use client'` and starts from a `<Spinner>`.
2. **Hydration mismatch** from rendering prices/times during SSR — avoid by loading in `useEffect`.
3. **401** is handled centrally in `lib/api.ts`. Don't re-handle it.
4. **Money arrives as strings** — `Number()` only for display.
5. **WebSocket in StrictMode** connects twice in dev → always return the cleanup from `useEffect` (`useLivePrices` already does).
6. **Chart data must be ascending and unique by `time`** or lightweight-charts throws — sort and de-dupe client-side too, cheap insurance.
7. **Empty chart** → "No trading activity yet" panel, not a broken canvas.
8. **Chart resize** — `autoSize: true` handles it; still call `chart.remove()` in cleanup or you leak canvases on every navigation.
9. **Numbers overflowing the layout** → the `.num` class (tabular figures) is already in `globals.css`; 2 dp for display.
10. **503 from the gateway** → "Service temporarily unavailable" with a retry button.

---

# SECTION 5 — TWO SHARED TOOLS YOU ALSO OWN

## 5.1 `frontend/mock-api.py` — build the UI before the backend exists (**do this in hour 1**)
~120 lines of FastAPI on port 8000 implementing **every** route in §1.6 from in-memory dicts:
* register/login return a real JWT signed with the same `JWT_SECRET`;
* balance / deposit / portfolio / orders / notifications from dicts;
* candles from a seeded random walk (correct shape: epoch-second `time`, numeric OHLC);
* `POST /orders` marks the order `FILLED` after 2 s and mutates the portfolio, so the whole journey is exercisable;
* `/ws/market` pushes a random-walk tick every second.

**B and C use this too** — ship it early and tell them. When the real stack comes up at hour 18, nothing changes but the process behind port 8000.

## 5.2 `infra/seed/seed_market_data.py` — history so the charts aren't blank
Writes **directly into the primary** (`market_db`, host port 5436):
* insert the 8 `symbols` rows from `common.symbols.SYMBOLS` / `SEED_PRICES`;
* generate 2 days of 1-minute candles per symbol by random walk (σ ≈ 0.0006 per minute, **seeded RNG so runs are reproducible**), plus the derived 5-minute candles;
* set `md:last_price:{SYMBOL}` in Redis to each last close;
* **idempotent** — guard on `SELECT count(*) FROM candles` so `make seed` twice doesn't double-seed.

---

# SECTION 6 — LOCAL DEV & TESTING ALONE

```yaml
# services/market-data-service/docker-compose.dev.yml
services:
  postgres: { image: postgres:16-alpine, environment: {POSTGRES_USER: mse, POSTGRES_PASSWORD: mse_pw, POSTGRES_DB: market_db}, ports: ["5436:5432"] }
  redis:    { image: redis:7-alpine, ports: ["6379:6379"] }
  rabbitmq: { image: rabbitmq:3.13-management-alpine, ports: ["5672:5672","15672:15672"] }
```
Leave `DATABASE_REPLICA_URL` empty locally — the fallback puts reads on the primary and everything works. Team A wires the real replica.
PowerShell: `$env:PYTHONPATH=".;../../libs"; uvicorn app.main:app --reload --port 8005`
Frontend: `cd frontend; npm install; npm run dev` with `.env.local` copied from `.env.local.example`.

**`services/market-data-service/scripts/fake_trades.py`** — publish N `trade.executed` events as a random walk, 200 ms apart, so charts move and you can watch the WebSocket in the browser. Also your backup demo if Team C is late.

**`selfcheck.py` — Market Data:**
```
floor_bucket(12:03:47,1) == 12:03:00     floor_bucket(12:03:47,5) == 12:00:00
floor_bucket(12:05:00,5) == 12:05:00     floor_bucket(23:59:59,5) == 23:55:00
first trade of a bucket   -> o=h=l=c=price, volume=qty
second trade higher       -> high rises, OPEN UNCHANGED, close=new, volume sums
second trade lower        -> low falls, open unchanged
same trade_id twice       -> one trade row, volume counted once
gap fill buckets 1,2,5 limit 10 -> 5 candles, 3 and 4 flat at candle-2's close, volume 0
candles output: strictly ascending unique epoch-second ints, OHLC are numbers
unknown symbol -> 404
```
**`selfcheck.py` — Portfolio:**
```
reserve 10 shares with no holding -> 409
BUY 10 @ 100  -> qty 10, avg 100
BUY 10 @ 200  -> qty 20, avg 150
reserve 20    -> ok, available 0 ; reserve 1 more -> 409
SELL 10 @ 180 -> qty 10, realized 300, avg STILL 150
replay the same trade -> unchanged
md:last_price:AAPL = 160 -> unrealized 100 ; delete the key -> unrealized 0, price_stale true
release a cancelled order twice -> second returns 0
```

---

# SECTION 7 — HOUR-BY-HOUR PLAN (24 h)

| Hours | Work |
|---|---|
| 0–1 | Pull repo, read `libs/common` and the frontend shell, start dev containers. |
| 1–2 | **`frontend/mock-api.py`** — tell B and C it's up. Everyone is now unblocked, including you. |
| 2–7 | **Market Data**: models, alembic, read/write split, trade handler, candle upsert, all REST endpoints, gap fill. `selfcheck.py` green. |
| 7–9 | WebSocket + Redis pub/sub fan-out + `tick_pump` + cache warm-up + Dockerfile. **Push `feat/team-d-market-portfolio`.** |
| 9–12 | **Portfolio Service**: holdings, avg cost / realized / unrealized, share reservations, event handlers. `selfcheck.py` green. Dockerfile. **Push.** |
| 12–13 | `infra/seed/seed_market_data.py`. |
| 13–16 | Frontend: `<CandleChart>` + `/market/[symbol]` against the mock API. **The chart is the demo — get it right early.** |
| 16–18 | `/portfolio` page + `<HoldingsTable>`. **Push — Team A's merge window.** |
| 18–21 | Point at the real gateway. Fix contract mismatches. On call for Team A. |
| 21–23 | Polish: loading skeletons, empty states, responsive layout. `SERVICE_NOTES.md` ×2. Screenshots: chart with live ticks, portfolio P&L, and the startup log proving `pg_is_in_recovery() = true` on the read engine. |
| 23–24 | Freeze. |

---

# SECTION 8 — DEFINITION OF DONE

- [ ] Both services build from the repo root; `/health`, `/ready`, `/docs` green.
- [ ] Startup logs prove reads go to the replica (`pg_is_in_recovery() = true`) and writes to the primary.
- [ ] Both `selfcheck.py` scripts pass every case in §6.
- [ ] One trade event produces, within a second: a `trades` row, updated **1m and 5m** candles, `md:last_price` set, a tick on `md:ticks`, a message on every open WebSocket, and both users' holdings updated.
- [ ] Replaying the same trade event twice changes no number anywhere.
- [ ] `/market/candles` returns ascending unique epoch-second `time` values, numeric OHLC, no gaps.
- [ ] `insufficient shares` (409) provably blocks a naked short.
- [ ] Chart renders history and moves live; portfolio shows correct avg cost, unrealized and realized P&L.
- [ ] `mock-api.py` and `seed_market_data.py` both work and are idempotent.
- [ ] `SERVICE_NOTES.md` for both services, including the documented "candles use numbers, everything else uses strings" deviation.
