"""End-to-end smoke test for Team B's slice (User + Account) through the
gateway. Talks only to http://localhost:8000/api/... — exactly what the
frontend does — so a pass here means the gateway routing, JWT plumbing, and
both services are wired together correctly.

Run against a running stack:
    docker compose up -d
    python scripts/smoke_test.py
"""

import sys
import uuid

import httpx

API_BASE = "http://localhost:8000"
FAILED = False


def check(label: str, condition: bool, detail: str = "") -> None:
    global FAILED
    if condition:
        print(f"  OK  {label}")
    else:
        FAILED = True
        print(f"FAIL  {label}  {detail}")


def run() -> None:
    print("=== TEAM B SMOKE TEST (User + Account, via gateway) ===")

    uid = str(uuid.uuid4())[:8]
    username = f"smoke_{uid}"
    email = f"smoke_{uid}@example.com"
    password = "smoke-test-password"

    with httpx.Client(base_url=API_BASE, timeout=10.0) as client:
        try:
            client.get("/health")
        except httpx.ConnectError:
            print(f"Cannot reach the gateway at {API_BASE} — is `docker compose up` running?")
            sys.exit(2)

        print("\n1. Register")
        r = client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password, "full_name": "Smoke Test"},
        )
        check("register returns 201 with a token", r.status_code == 201, r.text)
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        print("\n2. Duplicate register is rejected")
        r = client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )
        check("duplicate register is 409", r.status_code == 409, r.text)

        print("\n3. Login (form)")
        r = client.post("/api/auth/login", data={"username": username, "password": password})
        check("form login succeeds", r.status_code == 200, r.text)

        print("\n4. Login (JSON)")
        r = client.post("/api/auth/login-json", json={"username": email, "password": password})
        check("json login by email succeeds", r.status_code == 200, r.text)

        print("\n5. Wrong password is rejected")
        r = client.post("/api/auth/login-json", json={"username": username, "password": "not-it"})
        check("wrong password is 401", r.status_code == 401, r.text)

        print("\n6. Profile")
        r = client.get("/api/users/me", headers=headers)
        check("GET /users/me returns the profile", r.status_code == 200 and r.json()["email"] == email, r.text)

        r = client.patch("/api/users/me", headers=headers, json={"full_name": "Smoke Tester"})
        check("PATCH /users/me updates full_name", r.status_code == 200 and r.json()["full_name"] == "Smoke Tester", r.text)

        print("\n7. Unauthenticated access is rejected")
        r = client.get("/api/users/me")
        check("GET /users/me without a token is 401", r.status_code == 401, r.text)

        print("\n8. Initial balance is zero")
        r = client.get("/api/account/balance", headers=headers)
        check("balance starts at 0.0000", r.status_code == 200 and r.json()["cash_balance"] == "0.0000", r.text)

        print("\n9. Deposit funds")
        r = client.post("/api/account/deposit", headers=headers, json={"amount": "1000.00"})
        check("deposit succeeds", r.status_code == 200 and r.json()["cash_balance"] == "1000.0000", r.text)

        print("\n10. Invalid deposit is rejected")
        r = client.post("/api/account/deposit", headers=headers, json={"amount": "-50"})
        check("negative deposit is a 422", r.status_code == 422, r.text)

        print("\n11. Transaction ledger reflects the deposit")
        r = client.get("/api/account/transactions", headers=headers)
        txs = r.json() if r.status_code == 200 else []
        check("ledger has exactly one DEPOSIT entry", r.status_code == 200 and len(txs) == 1 and txs[0]["type"] == "DEPOSIT", r.text)

    print("\n=== SMOKE TEST " + ("FAILED" if FAILED else "PASSED") + " ===")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    run()
