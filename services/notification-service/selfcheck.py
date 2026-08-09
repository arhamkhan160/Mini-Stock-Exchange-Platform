"""Notification Service self-check.

Plain asserts, no pytest. Exercises the event handlers against a real Postgres
and Redis, then the HTTP routes through an in-process ASGI transport.

Run:
    docker compose -f services/notification-service/docker-compose.dev.yml up -d
    # PowerShell, from services/notification-service
    $env:PYTHONPATH=".;../../libs"
    $env:DATABASE_URL="postgresql+asyncpg://mse:mse_pw@localhost:5439/notification_db"
    $env:REDIS_URL="redis://localhost:6379/0"
    python selfcheck.py
"""

import asyncio
import sys
import uuid

import httpx
from sqlalchemy import delete, func, select

from common.db import Base
from common.events import ORDER_CANCELLED, ORDER_REJECTED, TRADE_EXECUTED, envelope
from common.security import create_access_token

from app import handlers
from app.deps import SessionLocal, engine, redis
from app.models import (
    ORDER_CANCELLED as N_CANCELLED,
    ORDER_FILLED,
    ORDER_PARTIALLY_FILLED,
    ORDER_REJECTED as N_REJECTED,
    Notification,
    ProcessedEvent,
)

U1 = str(uuid.uuid4())  # buyer
U2 = str(uuid.uuid4())  # seller
PASSED: list[str] = []


def ok(label: str) -> None:
    PASSED.append(label)
    print(f"  OK  {label}")


async def reset() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        await s.execute(delete(Notification))
        await s.execute(delete(ProcessedEvent))
        await s.commit()
    await redis.flushdb()


async def count(user_id: str | None = None, ntype: str | None = None) -> int:
    async with SessionLocal() as s:
        stmt = select(func.count()).select_from(Notification)
        if user_id:
            stmt = stmt.where(Notification.user_id == uuid.UUID(user_id))
        if ntype:
            stmt = stmt.where(Notification.type == ntype)
        return (await s.execute(stmt)).scalar_one()


def trade_event(buy_remaining: int, sell_remaining: int) -> dict:
    return envelope(
        TRADE_EXECUTED,
        {
            "trade_id": str(uuid.uuid4()),
            "symbol": "AAPL",
            "price": "195.5000",
            "quantity": 10,
            "buy_order_id": str(uuid.uuid4()),
            "sell_order_id": str(uuid.uuid4()),
            "buyer_user_id": U1,
            "seller_user_id": U2,
            "aggressor_side": "BUY",
            "buy_order_remaining": buy_remaining,
            "sell_order_remaining": sell_remaining,
            "buy_order_limit_price": "196.0000",
            "executed_at": "2026-08-09T12:00:00.000000Z",
        },
    )


async def main() -> None:
    # The mock emailer would call the User Service, which is not running here.
    handlers.send_mock_email = lambda *a, **k: asyncio.sleep(0)  # type: ignore[assignment]

    await reset()

    # ---- trade.executed, both sides complete ------------------------------
    filled = trade_event(0, 0)
    await handlers.handle_trade_executed(filled)
    assert await count() == 2, "one fill event must create exactly two notifications"
    assert await count(U1, ORDER_FILLED) == 1, "buyer must get a fully-filled alert"
    assert await count(U2, ORDER_FILLED) == 1, "seller must get a fully-filled alert"
    ok("trade.executed creates one notification per side")

    # ---- replay must be a no-op ------------------------------------------
    await handlers.handle_trade_executed(filled)
    assert await count() == 2, "replaying the same event must not duplicate alerts"
    ok("duplicate trade.executed is ignored (redis + processed_events)")

    # ---- replay with the Redis marker gone (processed_events must catch it)
    await redis.flushdb()
    await handlers.handle_trade_executed(filled)
    assert await count() == 2, "processed_events must stop a replay after a redis flush"
    ok("duplicate survives a redis flush via processed_events")

    # ---- partial fill -----------------------------------------------------
    await handlers.handle_trade_executed(trade_event(5, 0))
    assert await count(U1, ORDER_PARTIALLY_FILLED) == 1, "buyer with remainder must be PARTIALLY_FILLED"
    assert await count(U2, ORDER_FILLED) == 2, "seller with no remainder is fully filled"
    ok("partial fill is typed ORDER_PARTIALLY_FILLED for the incomplete side")

    # ---- order.cancelled: nothing worth reporting -------------------------
    before = await count()
    await handlers.handle_order_cancelled(
        envelope(ORDER_CANCELLED, {
            "order_id": str(uuid.uuid4()), "user_id": U1, "symbol": "AAPL", "side": "BUY",
            "cancelled_quantity": 0, "reason": "IOC_REMAINDER",
            "cancelled_at": "2026-08-09T12:00:01.000000Z",
        })
    )
    assert await count() == before, "IOC remainder of 0 must produce no notification"
    ok("order.cancelled with IOC_REMAINDER qty 0 is silent")

    # ---- order.cancelled: user requested ----------------------------------
    await handlers.handle_order_cancelled(
        envelope(ORDER_CANCELLED, {
            "order_id": str(uuid.uuid4()), "user_id": U1, "symbol": "AAPL", "side": "BUY",
            "cancelled_quantity": 7, "reason": "USER_REQUEST",
            "cancelled_at": "2026-08-09T12:00:02.000000Z",
        })
    )
    assert await count(U1, N_CANCELLED) == 1, "user cancel must notify"
    ok("order.cancelled with USER_REQUEST notifies the owner")

    # ---- order.rejected ---------------------------------------------------
    await handlers.handle_order_rejected(
        envelope(ORDER_REJECTED, {
            "order_id": str(uuid.uuid4()), "user_id": U1, "symbol": "AAPL",
            "reason": "insufficient buying power",
            "rejected_at": "2026-08-09T12:00:03.000000Z",
        })
    )
    async with SessionLocal() as s:
        row = (await s.execute(
            select(Notification).where(Notification.type == N_REJECTED)
        )).scalar_one()
    assert "insufficient buying power" in row.message, "reject reason must reach the user"
    ok("order.rejected surfaces the backend reason verbatim")

    # ---- HTTP routes ------------------------------------------------------
    from app.main import app  # imported late: no lifespan, so no broker needed

    expected_unread = await count(U1)
    token1 = create_access_token(U1, "u1@mse.local", "u1")
    token2 = create_access_token(U2, "u2@mse.local", "u2")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        h1 = {"Authorization": f"Bearer {token1}"}
        h2 = {"Authorization": f"Bearer {token2}"}

        r = await client.get("/notifications/unread-count", headers=h1)
        assert r.status_code == 200 and r.json()["count"] == expected_unread, r.text
        ok(f"unread-count reports {expected_unread} for the buyer")

        r = await client.get("/notifications", headers=h1)
        assert r.status_code == 200 and len(r.json()) == expected_unread, r.text
        assert r.json()[0]["created_at"] >= r.json()[-1]["created_at"], "must be newest first"
        ok("listing is scoped to the caller and ordered newest first")

        r = await client.get("/notifications")
        assert r.status_code == 401, "listing without a token must be 401"
        ok("unauthenticated listing is rejected")

        async with SessionLocal() as s:
            u1_row = (await s.execute(
                select(Notification).where(Notification.user_id == uuid.UUID(U1)).limit(1)
            )).scalar_one()
        r = await client.post(f"/notifications/{u1_row.id}/read", headers=h2)
        assert r.status_code == 404, "another user's notification must 404, not 403"
        ok("marking someone else's notification read returns 404")

        r = await client.post(f"/notifications/{u1_row.id}/read", headers=h1)
        assert r.status_code == 200 and r.json()["is_read"] is True, r.text
        r = await client.post(f"/notifications/{u1_row.id}/read", headers=h1)
        assert r.status_code == 200, "marking read twice must be idempotent"
        ok("mark-read works and is idempotent")

        r = await client.post("/notifications/read-all", headers=h1)
        assert r.status_code == 200 and r.json()["marked"] == expected_unread - 1, r.text
        r = await client.get("/notifications/unread-count", headers=h1)
        assert r.json()["count"] == 0, "read-all must clear the badge"
        ok("read-all clears every unread notification")

    await redis.aclose()
    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed.")
