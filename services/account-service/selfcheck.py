"""Account Service self-check.

Plain asserts, no pytest. Exercises the event handlers against a real Postgres
and Redis, then the HTTP routes (including the internal reservation
endpoints) through an in-process ASGI transport. Encodes the exact numbers
from TEAM_B_IDENTITY_AND_ASSETS.md §6:

    deposit 10000 -> available 10000
    reserve 2000 (order X) -> available 8000, held 2000
    reserve order X again -> still held 2000
    reserve 9000 -> 409
    emit trade.executed filling 5 @ 200 (limit 210, remaining 5) -> cash 9000, held 950
    emit the final fill (remaining 0) -> cash 8000, held 0
    emit the same event twice -> numbers unchanged
    release a cancelled order twice -> second returns "0.0000"
    deposit -1 -> 400

Run:
    docker compose up -d postgres-account redis rabbitmq
    # PowerShell, from services/account-service
    $env:PYTHONPATH=".;../../libs"
    $env:DATABASE_URL="postgresql+asyncpg://mse:mse_pw@localhost:5434/account_db"
    $env:REDIS_URL="redis://localhost:6379/0"
    $env:SERVICE_PORT="8002"
    $env:SERVICE_NAME="account-service"
    python selfcheck.py
"""

import asyncio
import sys
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import delete, select, text

from common.config import settings
from common.db import Base
from common.events import ORDER_CANCELLED, ORDER_REJECTED, TRADE_EXECUTED, envelope
from common.security import create_access_token

from app import handlers
from app.deps import SessionLocal, engine, redis
from app.models import Account, ProcessedEvent, Reservation, Transaction

BUYER = str(uuid.uuid4())
SELLER = str(uuid.uuid4())
ORDER_X = str(uuid.uuid4())
PASSED: list[str] = []


def ok(label: str) -> None:
    PASSED.append(label)
    print(f"  OK  {label}")


INITIAL_REVISION = "0001"


def _require_destructive_optin() -> None:
    """This self-check DELETES every row in its tables.

    Pointed at the shared development database it silently destroys seeded
    demo data, so it refuses to run unless the caller opts in explicitly.

        SELFCHECK_DESTRUCTIVE=1 python selfcheck.py

    Prefer a throwaway database:
        DATABASE_URL=postgresql+asyncpg://mse:mse_pw@localhost:PORT/scratch_db
    """
    import os
    import sys

    if os.getenv("SELFCHECK_DESTRUCTIVE") == "1":
        return
    print(
        "REFUSING TO RUN: this self-check wipes its tables and would destroy any "
        "seeded data in the database it is pointed at. Re-run with "
        "SELFCHECK_DESTRUCTIVE=1 if that is what you want, ideally against a "
        "scratch DATABASE_URL."
    )
    sys.exit(2)


async def reset() -> None:
    _require_destructive_optin()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version "
                 "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        )
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:rev) "
                 "ON CONFLICT (version_num) DO NOTHING"),
            {"rev": INITIAL_REVISION},
        )
    async with SessionLocal() as s:
        await s.execute(delete(Transaction))
        await s.execute(delete(Reservation))
        await s.execute(delete(ProcessedEvent))
        await s.execute(delete(Account))
        await s.commit()
    await redis.flushdb()


async def balance_of(user_id: str) -> Account:
    async with SessionLocal() as s:
        return (await s.execute(select(Account).where(Account.user_id == uuid.UUID(user_id)))).scalar_one()


async def reservation_of(order_id: str) -> Reservation | None:
    async with SessionLocal() as s:
        return (
            await s.execute(select(Reservation).where(Reservation.order_id == uuid.UUID(order_id)))
        ).scalars().first()


def trade_event(buy_order_id: str, price: str, quantity: int, limit_price: str | None, buy_remaining: int) -> dict:
    return envelope(
        TRADE_EXECUTED,
        {
            "trade_id": str(uuid.uuid4()),
            "symbol": "AAPL",
            "price": price,
            "quantity": quantity,
            "buy_order_id": buy_order_id,
            "sell_order_id": str(uuid.uuid4()),
            "buyer_user_id": BUYER,
            "seller_user_id": SELLER,
            "aggressor_side": "BUY",
            "buy_order_remaining": buy_remaining,
            "sell_order_remaining": 0,
            "buy_order_limit_price": limit_price,
            "executed_at": "2026-08-10T12:00:00.000000Z",
        },
    )


async def main() -> None:
    await reset()

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        h_buyer = {"Authorization": f"Bearer {create_access_token(BUYER, 'buyer@mse.local', 'buyer')}"}
        h_seller = {"Authorization": f"Bearer {create_access_token(SELLER, 'seller@mse.local', 'seller')}"}
        h_internal = {"X-Internal-Key": settings.INTERNAL_API_KEY}

        r = await client.get("/health")
        assert r.status_code == 200, r.text
        ok("/health is a pure liveness check")

        r = await client.get("/account/balance", headers=h_buyer)
        assert r.status_code == 200 and r.json()["cash_balance"] == "0.0000" and r.json()["currency"] == "USD", r.text
        ok("a brand-new account starts at zero USD (get-or-create)")

        # ---- deposit 10000 -> available 10000 ------------------------------
        r = await client.post("/account/deposit", headers=h_buyer, json={"amount": "10000.00"})
        assert r.status_code == 200 and r.json()["available_balance"] == "10000.0000", r.text
        ok("deposit 10000 -> available 10000")

        r = await client.post("/account/deposit", headers=h_buyer, json={"amount": "-1"})
        assert r.status_code == 400, r.text
        ok("deposit -1 -> 400")

        r = await client.post("/account/deposit", headers=h_buyer, json={"amount": "10.00001"})
        assert r.status_code == 400, r.text
        ok("deposit with more than 2 decimal places -> 400")

        r = await client.get("/account/transactions", headers=h_buyer)
        assert r.status_code == 200 and len(r.json()) == 1 and r.json()[0]["type"] == "DEPOSIT", r.text
        ok("deposit is recorded in the ledger")

        r = await client.get("/account/transactions?type=deposit", headers=h_buyer)
        assert r.status_code == 200 and len(r.json()) == 1, r.text
        ok("transaction listing can filter by type (case-insensitive)")

        # ---- reserve 2000 (order X) -> available 8000, held 2000 -----------
        r = await client.post(
            "/internal/reservations", headers=h_internal,
            json={"order_id": ORDER_X, "user_id": BUYER, "amount": "2000.0000"},
        )
        assert r.status_code == 201 and r.json()["status"] == "HELD", r.text
        acct = await balance_of(BUYER)
        assert acct.held_balance == Decimal("2000.0000"), acct.held_balance
        assert acct.cash_balance == Decimal("10000.0000"), "reserving must not touch cash_balance"
        ok("reserve 2000 (order X) -> available 8000, held 2000")

        # ---- reserve order X again -> still held 2000 (idempotent) --------
        r = await client.post(
            "/internal/reservations", headers=h_internal,
            json={"order_id": ORDER_X, "user_id": BUYER, "amount": "2000.0000"},
        )
        assert r.status_code == 201, r.text
        acct = await balance_of(BUYER)
        assert acct.held_balance == Decimal("2000.0000"), "a retried reservation call must be idempotent on order_id"
        ok("re-reserving order X is idempotent — still held 2000")

        # ---- reserve 9000 -> 409 (only 8000 available) ---------------------
        r = await client.post(
            "/internal/reservations", headers=h_internal,
            json={"order_id": str(uuid.uuid4()), "user_id": BUYER, "amount": "9000.00"},
        )
        assert r.status_code == 409, r.text
        ok("reserve 9000 -> 409 (insufficient buying power)")

        # ---- partial fill: 5 @ 200, limit 210, remaining 5 -----------------
        env = trade_event(ORDER_X, price="200.0000", quantity=5, limit_price="210.0000", buy_remaining=5)
        await handlers.handle_trade_executed(env)
        acct = await balance_of(BUYER)
        assert acct.cash_balance == Decimal("9000.0000"), acct.cash_balance
        assert acct.held_balance == Decimal("950.0000"), acct.held_balance
        res = await reservation_of(ORDER_X)
        assert res.status == "HELD" and res.remaining == Decimal("950.0000"), res
        ok("partial fill 5 @ 200 (limit 210, remaining 5) -> cash 9000, held 950")

        # replaying the exact same event must be a no-op
        await handlers.handle_trade_executed(env)
        acct2 = await balance_of(BUYER)
        assert acct2.cash_balance == acct.cash_balance and acct2.held_balance == acct.held_balance
        ok("replaying the same trade.executed event changes nothing")

        # survives a redis flush too (processed_events is the real authority)
        await redis.flushdb()
        await handlers.handle_trade_executed(env)
        acct3 = await balance_of(BUYER)
        assert acct3.cash_balance == acct.cash_balance and acct3.held_balance == acct.held_balance
        ok("the duplicate still no-ops after a redis flush (processed_events)")

        # ---- final fill: remaining 0 -> cash 8000, held 0 -------------------
        final_env = trade_event(ORDER_X, price="200.0000", quantity=5, limit_price="210.0000", buy_remaining=0)
        await handlers.handle_trade_executed(final_env)
        acct = await balance_of(BUYER)
        assert acct.cash_balance == Decimal("8000.0000"), acct.cash_balance
        assert acct.held_balance == Decimal("0.0000"), acct.held_balance
        res = await reservation_of(ORDER_X)
        assert res.status in ("CONSUMED", "RELEASED"), res.status
        ok("final fill (remaining 0) -> cash 8000, held 0")

        # ---- order.cancelled / order.rejected release -----------------------
        order_y = str(uuid.uuid4())
        await client.post(
            "/internal/reservations", headers=h_internal,
            json={"order_id": order_y, "user_id": BUYER, "amount": "500.0000"},
        )
        await handlers.handle_order_cancelled(
            envelope(ORDER_CANCELLED, {
                "order_id": order_y, "user_id": BUYER, "symbol": "AAPL", "side": "BUY",
                "cancelled_quantity": 5, "reason": "USER_REQUEST", "cancelled_at": "2026-08-10T12:01:00Z",
            })
        )
        acct = await balance_of(BUYER)
        assert acct.held_balance == Decimal("0.0000"), acct.held_balance
        ok("order.cancelled releases the remaining hold")

        # cancelling/rejecting an order with no reservation is a harmless no-op
        await handlers.handle_order_rejected(
            envelope(ORDER_REJECTED, {
                "order_id": str(uuid.uuid4()), "user_id": SELLER, "symbol": "AAPL",
                "reason": "no liquidity", "rejected_at": "2026-08-10T12:02:00Z",
            })
        )
        ok("releasing a reservation that never existed does not raise")

        # ---- REST release endpoint, twice -----------------------------------
        order_z = str(uuid.uuid4())
        await client.post(
            "/internal/reservations", headers=h_internal,
            json={"order_id": order_z, "user_id": BUYER, "amount": "200.0000"},
        )
        r = await client.post(f"/internal/reservations/{order_z}/release", headers=h_internal)
        assert r.status_code == 200 and r.json()["released"] == "200.0000", r.text
        ok("POST /internal/reservations/{id}/release frees the full hold")

        r = await client.post(f"/internal/reservations/{order_z}/release", headers=h_internal)
        assert r.status_code == 200 and r.json()["released"] == "0.0000", r.text
        ok("releasing an already-released order_id is idempotent — second call returns 0.0000")

        r = await client.post(f"/internal/reservations/{uuid.uuid4()}/release", headers=h_internal)
        assert r.status_code == 404, r.text
        ok("releasing an order_id that was never reserved at all is 404")

        # ---- auth/authorization ----------------------------------------------
        r = await client.get("/account/balance")
        assert r.status_code == 401, r.text
        ok("balance without a token is 401")

        r = await client.post("/internal/reservations", json={"order_id": str(uuid.uuid4()), "user_id": BUYER, "amount": "1"})
        assert r.status_code == 403, r.text
        ok("internal reservation endpoint without X-Internal-Key is 403")

        r = await client.get("/account/balance", headers=h_seller)
        assert r.status_code == 200 and r.json()["user_id"] == SELLER, r.text
        ok("balance is scoped to the caller from the JWT")

        r = await client.get(f"/internal/accounts/{BUYER}", headers=h_internal)
        assert r.status_code == 200 and r.json()["cash_balance"] == "8000.0000", r.text
        ok("GET /internal/accounts/{id} returns any user's balance for debugging")

    await redis.aclose()
    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed.")
