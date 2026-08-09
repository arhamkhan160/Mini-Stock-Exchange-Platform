"""User Service self-check.

Plain asserts, no pytest. Drives the HTTP routes through an in-process ASGI
transport against a real Postgres.

Run:
    docker compose up -d postgres-user
    # PowerShell, from services/user-service
    $env:PYTHONPATH=".;../../libs"
    $env:DATABASE_URL="postgresql+asyncpg://mse:mse_pw@localhost:5433/user_db"
    $env:SERVICE_PORT="8001"
    $env:SERVICE_NAME="user-service"
    python selfcheck.py
"""

import asyncio
import sys
import uuid

import httpx
from sqlalchemy import delete, text

from common.db import Base
from common.config import settings

from app.deps import SessionLocal, engine
from app.models import User

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
        await s.execute(delete(User))
        await s.commit()


async def main() -> None:
    await reset()

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "ok", r.text
        ok("/health is a pure liveness check")

        r = await client.get("/ready")
        assert r.status_code == 200 and r.json()["database"] == "ok", r.text
        ok("/ready reports database ok")

        uid = str(uuid.uuid4())[:8]
        body = {
            "email": f"trader_{uid}@mse.local",
            "username": f"trader_{uid}",
            "password": "correct-horse-battery",
            "full_name": "Ada Trader",
        }
        r = await client.post("/auth/register", json=body)
        assert r.status_code == 201, r.text
        token = r.json()["access_token"]
        user_id = r.json()["user"]["id"]
        assert r.json()["user"]["email"] == body["email"]
        ok("register returns a token and echoes the new user")

        r = await client.post("/auth/register", json=body)
        assert r.status_code == 409, r.text
        ok("registering the same email/username twice is a 409")

        r = await client.post(
            "/auth/login",
            data={"username": body["username"], "password": body["password"]},
        )
        assert r.status_code == 200 and r.json()["access_token"], r.text
        ok("form login by username works")

        r = await client.post(
            "/auth/login",
            data={"username": body["email"], "password": body["password"]},
        )
        assert r.status_code == 200, r.text
        ok("form login by email works")

        r = await client.post(
            "/auth/login-json",
            json={"username": body["username"], "password": "wrong-password"},
        )
        assert r.status_code == 401, r.text
        assert "password" not in r.json()["detail"].lower() or "incorrect" in r.json()["detail"].lower()
        ok("wrong password on login-json is a generic 401")

        r = await client.post(
            "/auth/login-json",
            json={"username": body["username"], "password": body["password"]},
        )
        assert r.status_code == 200, r.text
        ok("JSON login works")

        headers = {"Authorization": f"Bearer {token}"}
        r = await client.get("/users/me", headers=headers)
        assert r.status_code == 200 and r.json()["username"] == body["username"], r.text
        ok("GET /users/me returns the caller's profile")

        r = await client.get("/users/me")
        assert r.status_code == 401, r.text
        ok("GET /users/me without a token is 401")

        r = await client.patch("/users/me", headers=headers, json={"full_name": "Ada T. Trader"})
        assert r.status_code == 200 and r.json()["full_name"] == "Ada T. Trader", r.text
        ok("PATCH /users/me updates full_name")

        r = await client.get(f"/internal/users/{user_id}", headers={"X-Internal-Key": settings.INTERNAL_API_KEY})
        assert r.status_code == 200 and r.json()["id"] == user_id, r.text
        ok("internal lookup by id works with the internal key")

        r = await client.get(f"/internal/users/{user_id}")
        assert r.status_code == 403, r.text
        ok("internal lookup without X-Internal-Key is 403")

        r = await client.get(
            f"/internal/users/{uuid.uuid4()}", headers={"X-Internal-Key": settings.INTERNAL_API_KEY}
        )
        assert r.status_code == 404, r.text
        ok("internal lookup of an unknown id is 404")

    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed.")
