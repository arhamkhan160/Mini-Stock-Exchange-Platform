"""User Service self-check.

Plain asserts, no pytest. Drives the HTTP routes through an in-process ASGI
transport against a real Postgres. Encodes the exact numbers from
TEAM_B_IDENTITY_AND_ASSETS.md §6:

    register -> 201 + token; register the same email uppercased -> 409;
    login by username and by email -> both work; wrong password -> 401;
    /users/me with the token -> same id; 100-byte password -> 400;
    username "ab" -> 400.

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

from common.config import settings
from common.db import Base

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
        email = f"Trader_{uid}@Mse.Local"
        body = {"email": email, "username": f"trader_{uid}", "password": "correcthorse1", "full_name": "  "}
        r = await client.post("/auth/register", json=body)
        assert r.status_code == 201, r.text
        token = r.json()["access_token"]
        user_id = r.json()["user"]["id"]
        assert r.json()["user"]["email"] == email.lower(), "email must be stored/returned lowercase"
        assert r.json()["user"]["full_name"] is None, "whitespace-only full_name must be stored as NULL"
        ok("register returns a token, lowercases the email, and nulls a blank full_name")

        r = await client.post("/auth/register", json={**body, "email": email.upper(), "username": f"other_{uid}"})
        assert r.status_code == 409, r.text
        ok("registering the same email uppercased is a 409")

        r = await client.post(
            "/auth/register", json={**body, "email": f"case_{uid}@mse.local", "username": body["username"].upper()}
        )
        assert r.status_code == 409, r.text
        ok("registering the same username with different case is a 409 (functional unique index)")

        r = await client.post(
            "/auth/login",
            data={"username": body["username"], "password": body["password"]},
        )
        assert r.status_code == 200 and r.json()["access_token"], r.text
        ok("form login by username works")

        r = await client.post(
            "/auth/login",
            data={"username": body["email"].upper(), "password": body["password"]},
        )
        assert r.status_code == 200, r.text
        ok("form login by email (any case) works")

        r = await client.post(
            "/auth/login",
            data={"username": body["username"].upper(), "password": body["password"]},
        )
        assert r.status_code == 200, r.text
        ok("form login by username (any case) works")

        r = await client.post(
            "/auth/login-json",
            json={"email_or_username": body["username"], "password": "wrong-password"},
        )
        assert r.status_code == 401, r.text
        wrong_pw_detail = r.json()["detail"]
        ok("wrong password on login-json is a generic 401")

        r = await client.post(
            "/auth/login-json",
            json={"email_or_username": "nobody-registered-with-this-name", "password": "wrong-password"},
        )
        assert r.status_code == 401 and r.json()["detail"] == wrong_pw_detail, r.text
        ok("unknown identifier returns the SAME 401 message as a wrong password (no enumeration)")

        r = await client.post(
            "/auth/login-json",
            json={"email_or_username": body["username"], "password": body["password"]},
        )
        assert r.status_code == 200, r.text
        ok("JSON login works")

        headers = {"Authorization": f"Bearer {token}"}
        r = await client.get("/users/me", headers=headers)
        assert r.status_code == 200 and r.json()["id"] == user_id, r.text
        ok("GET /users/me returns the caller's own id")

        r = await client.get("/users/me")
        assert r.status_code == 401, r.text
        ok("GET /users/me without a token is 401")

        r = await client.patch("/users/me", headers=headers, json={"full_name": "Ada T. Trader"})
        assert r.status_code == 200 and r.json()["full_name"] == "Ada T. Trader", r.text
        ok("PATCH /users/me updates full_name")

        new_username = f"renamed_{uid}"
        r = await client.patch("/users/me", headers=headers, json={"username": new_username})
        assert r.status_code == 200 and r.json()["username"] == new_username, r.text
        ok("PATCH /users/me updates username")

        # a second user colliding on the (now renamed) username case-insensitively
        r = await client.post(
            "/auth/register",
            json={
                "email": f"second_{uid}@mse.local",
                "username": f"second_{uid}",
                "password": "correcthorse1",
            },
        )
        assert r.status_code == 201, r.text
        second_token = r.json()["access_token"]
        r = await client.patch(
            "/users/me", headers={"Authorization": f"Bearer {second_token}"}, json={"username": new_username.upper()}
        )
        assert r.status_code == 409, r.text
        ok("PATCH /users/me to a username taken (any case) by someone else is a 409")

        r = await client.post(
            "/auth/change-password", headers=headers, json={"current_password": "wrong", "new_password": "newpassw0rd"}
        )
        assert r.status_code == 401, r.text
        ok("change-password with the wrong current password is 401")

        r = await client.post(
            "/auth/change-password",
            headers=headers,
            json={"current_password": body["password"], "new_password": "newpassw0rd"},
        )
        assert r.status_code == 200, r.text
        ok("change-password succeeds with the correct current password")

        r = await client.post("/auth/login-json", json={"email_or_username": new_username, "password": "newpassw0rd"})
        assert r.status_code == 200, r.text
        ok("login works with the new password after change-password")

        r = await client.post(
            "/auth/register", json={"email": f"longpw_{uid}@mse.local", "username": f"longpw_{uid}", "password": "a1" * 50}
        )
        assert r.status_code == 400, r.text
        ok("a 100-byte password is a 400")

        r = await client.post(
            "/auth/register", json={"email": f"shortun_{uid}@mse.local", "username": "ab", "password": "correcthorse1"}
        )
        assert r.status_code == 400, r.text
        ok('username "ab" is a 400')

        r = await client.post(
            "/auth/register",
            json={"email": f"nodigit_{uid}@mse.local", "username": f"nodigit_{uid}", "password": "onlyletters"},
        )
        assert r.status_code == 400, r.text
        ok("a password with no digit is a 400")

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

        r = await client.get(
            f"/internal/users?ids={user_id},{uuid.uuid4()},not-a-uuid",
            headers={"X-Internal-Key": settings.INTERNAL_API_KEY},
        )
        assert r.status_code == 200 and len(r.json()) == 1 and r.json()[0]["id"] == user_id, r.text
        ok("batch internal lookup returns only the ids that exist, ignoring malformed ones")

        r = await client.get(
            f"/internal/users?ids={','.join(str(uuid.uuid4()) for _ in range(101))}",
            headers={"X-Internal-Key": settings.INTERNAL_API_KEY},
        )
        assert r.status_code == 400, r.text
        ok("batch internal lookup over 100 ids is a 400")

    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed.")
