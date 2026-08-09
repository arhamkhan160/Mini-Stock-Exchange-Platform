"""Account Service self-check.

Plain asserts, no pytest. Exercises the event handlers against a real Postgres
and Redis, then the HTTP routes (including the internal reservation
endpoints) through an in-process ASGI transport.

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
PASSED: list[str] = []


def ok(label: str) -> None:
    PASSED.append(label)
    print(f"  OK  {label}")


INITIAL_REVISION = "0001"


async def reset() -> None:
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
        row = (await s.execute(select(Account).where(Account.user_id == uuid.UUID(user_id)))).scalar_one()
        return row


async def reservation_of(order_id: str) -> Reservation | None:
    async with SessionLocal() as s:
        return (
            await s.execute(select(Reservation).where(Reservation.order_id == uuid.UUID(order_id)))
        ).scalars().first()


def trade_event(buy_order_id: str, price: str, quantity: int, limit_price: str | None,
                 buy_remaining: int, sell_remaining: int = 0) -> dict:
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
            "sell_order_remaining": sell_remaining,
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
        assert r.status_code == 200 and r.json()["cash_balance"] == "0.0000", r.text
        ok("a brand-new account starts at zero (get-or-create)")

        r = await client.post("/account/deposit", headers=h_buyer, json={"amount": "10000.00"})
        assert r.status_code == 200 and r.json()["cash_balance"] == "10000.0000", r.text
        ok("deposit increases cash_balance")

        r = await client.post("/account/deposit", headers=h_buyer, json={"amount": "-5"})
        assert r.status_code == 422, r.text
        ok("negative deposit is rejected")

        r = await client.get("/account/transactions", headers=h_buyer)
        assert r.status_code == 200 and len(r.json()) == 1 and r.json()[0]["type"] == "DEPOSIT", r.text
        ok("deposit is recorded in the ledger")

        # ---- internal reservation: LIMIT buy order holds price*qty --------
        order_id = str(uuid.uuid4())
        r = await client.post(
            "/internal/reservations", headers=h_internal,
            json={"order_id": order_id, "user_id": BUYER, "amount": "1000.0000"},
        )
        assert r.status_code == 201 and r.json()["status"] == "HELD", r.text
        ok("reserving funds for a new order succeeds")

        acct = await balance_of(BUYER)
        assert acct.held_balance == Decimal("1000.0000"), acct.held_balance
        assert acct.cash_balance == Decimal("10000.0000"), "reserving must not touch cash_balance"
        ok("reservation moves cash into held_balance without touching cash_balance")

        # replaying the same reservation call must not double-hold
        r = await client.post(
            "/internal/reservations", headers=h_internal,
            json={"order_id": order_id, "user_id": BUYER, "amount": "1000.0000"},
        )
        assert r.status_code == 201, r.text
        acct = await balance_of(BUYER)
        assert acct.held_balance == Decimal("1000.0000"), "a retried reservation call must be idempotent on order_id"
        ok("re-reserving the same order_id is idempotent")

        # available balance rejects an over-limit reservation
        r = await client.post(
            "/internal/reservations", headers=h_internal,
            json={"order_id": str(uuid.uuid4()), "user_id": BUYER, "amount": "50000.00"},
        )
        assert r.status_code == 409, r.text
        ok("reserving more than the available balance is a 409")

        # ---- trade.executed: full fill at the limit price releases nothing,
        # consumes the whole reservation and charges cash -------------------
        await handlers.handle_trade_executed(
            trade_event(order_id, price="10.0000", quantity=100, limit_price="10.0000", buy_remaining=0)
        )
        acct = await balance_of(BUYER)
        assert acct.cash_balance == Decimal("9000.0000"), acct.cash_balance
        assert acct.held_balance == Decimal("0.0000"), acct.held_balance
        assert await reservation_of(order_id) is None, "a fully-filled order must have no reservation left"
        seller_acct = await balance_of(SELLER)
        assert seller_acct.cash_balance == Decimal("1000.0000"), seller_acct.cash_balance
        ok("full fill at the limit price settles both sides and clears the hold")

        # replaying the same trade.executed event must be a no-op
        acct_before = await balance_of(BUYER)
        env = trade_event(order_id, price="10.0000", quantity=100, limit_price="10.0000", buy_remaining=0)
        await handlers.handle_trade_executed(env)
        await handlers.handle_trade_executed(env)  # exact same envelope, same event_id
        acct_after = await balance_of(BUYER)
        assert acct_after.cash_balance == acct_before.cash_balance, "duplicate trade.executed must not double-charge"
        ok("duplicate trade.executed (same event_id) is a no-op")

        # a replay surviving a redis flush must still be caught by processed_events
        await redis.flushdb()
        await handlers.handle_trade_executed(env)
        acct_after2 = await balance_of(BUYER)
        assert acct_after2.cash_balance == acct_before.cash_balance, "processed_events must stop a replay after a redis flush"
        ok("duplicate survives a redis flush via processed_events")

        # ---- price improvement: fill below the limit releases the slack ---
        order_id2 = str(uuid.uuid4())
        await client.post(
            "/internal/reservations", headers=h_internal,
            json={"order_id": order_id2, "user_id": BUYER, "amount": "500.0000"},  # held at limit 10.00 * 50
        )
        acct_before = await balance_of(BUYER)
        await handlers.handle_trade_executed(
            trade_event(order_id2, price="9.0000", quantity=50, limit_price="10.0000", buy_remaining=0)
        )
        acct_after = await balance_of(BUYER)
        assert acct_after.cash_balance == acct_before.cash_balance - Decimal("450.0000"), acct_after.cash_balance
        assert acct_after.held_balance == acct_before.held_balance - Decimal("500.0000"), "the full hold must clear on the final fill"
        ok("filling below the limit price charges the execution price and releases the improvement")

        # ---- partial fill: reservation shrinks, held stays for the rest ---
        order_id3 = str(uuid.uuid4())
        await client.post(
            "/internal/reservations", headers=h_internal,
            json={"order_id": order_id3, "user_id": BUYER, "amount": "1000.0000"},  # 100 @ 10.00
        )
        await handlers.handle_trade_executed(
            trade_event(order_id3, price="10.0000", quantity=40, limit_price="10.0000", buy_remaining=60)
        )
        res = await reservation_of(order_id3)
        assert res is not None and res.amount == Decimal("600.0000"), res
        ok("a partial fill shrinks the reservation but leaves it open")

        # ---- order.cancelled releases whatever remains ---------------------
        acct_before = await balance_of(BUYER)
        await handlers.handle_order_cancelled(
            envelope(ORDER_CANCELLED, {
                "order_id": order_id3, "user_id": BUYER, "symbol": "AAPL", "side": "BUY",
                "cancelled_quantity": 60, "reason": "USER_REQUEST", "cancelled_at": "2026-08-10T12:01:00Z",
            })
        )
        acct_after = await balance_of(BUYER)
        assert acct_after.held_balance == acct_before.held_balance - Decimal("600.0000"), acct_after.held_balance
        assert await reservation_of(order_id3) is None
        ok("order.cancelled releases the remaining hold")

        # cancelling/rejecting an order with no reservation (e.g. a SELL) is a no-op, not an error
        await handlers.handle_order_rejected(
            envelope(ORDER_REJECTED, {
                "order_id": str(uuid.uuid4()), "user_id": SELLER, "symbol": "AAPL",
                "reason": "no liquidity", "rejected_at": "2026-08-10T12:02:00Z",
            })
        )
        ok("releasing a reservation that never existed does not raise")

        # ---- internal release endpoint, called directly ---------------------
        order_id4 = str(uuid.uuid4())
        await client.post(
            "/internal/reservations", headers=h_internal,
            json={"order_id": order_id4, "user_id": BUYER, "amount": "200.0000"},
        )
        r = await client.post(f"/internal/reservations/{order_id4}/release", headers=h_internal)
        assert r.status_code == 200 and r.json()["released_amount"] == "200.0000", r.text
        ok("POST /internal/reservations/{id}/release frees the full hold")

        r = await client.post(f"/internal/reservations/{order_id4}/release", headers=h_internal)
        assert r.status_code == 200 and r.json()["released_amount"] == "0.0000", r.text
        ok("releasing an already-released order_id is idempotent, not a 404")

        # ---- auth/authorization -------------------------------------------
        r = await client.get("/account/balance")
        assert r.status_code == 401, r.text
        ok("balance without a token is 401")

        r = await client.post("/internal/reservations", json={"order_id": str(uuid.uuid4()), "user_id": BUYER, "amount": "1"})
        assert r.status_code == 403, r.text
        ok("internal reservation endpoint without X-Internal-Key is 403")

        r = await client.get("/account/balance", headers=h_seller)
        assert r.status_code == 200 and r.json()["user_id"] == SELLER, r.text
        ok("balance is scoped to the caller from the JWT")

    await redis.aclose()
    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed.")
