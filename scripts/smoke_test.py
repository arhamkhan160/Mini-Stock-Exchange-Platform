"""End-to-end smoke test — the team's merge gate and demo script.

Written against the shared contract (TEAM_B_IDENTITY_AND_ASSETS.md §1, §5.1),
not against any one team's implementation, so it can be written and reviewed
before every service is merged. Steps whose dependency didn't come up yet
(Order/Matching/Market-Data/Portfolio/Notification are owned by Teams A/C/D)
report FAIL rather than crash the run — this script always finishes and
prints a full report.

Pure httpx + websockets, no pytest. Prints OK/FAIL per step, exits non-zero
if anything failed.

Run against a running stack:
    docker compose up -d
    python scripts/smoke_test.py
"""

import sys
import time
import uuid

import httpx

try:
    import websockets
except ImportError:
    websockets = None  # step 20 degrades to a SKIP instead of crashing the run

API_BASE = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"
FAILED = 0
SKIPPED = 0


def ok(label: str) -> None:
    print(f"  OK  {label}")


def fail(label: str, detail: str = "") -> None:
    global FAILED
    FAILED += 1
    print(f"FAIL  {label}  {detail}")


def skip(label: str, reason: str) -> None:
    global SKIPPED
    SKIPPED += 1
    print(f"SKIP  {label}  ({reason})")


def register(client: httpx.Client, tag: str) -> dict | None:
    uid = str(uuid.uuid4())[:8]
    body = {
        "username": f"smoke_{tag}_{uid}",
        "email": f"smoke_{tag}_{uid}@example.com",
        "password": "smoke-test-pw1",
        "full_name": f"Smoke {tag.title()}",
    }
    r = client.post("/api/auth/register", json=body)
    if r.status_code != 201:
        fail(f"register {tag}", f"{r.status_code} {r.text}")
        return None
    data = r.json()
    data["headers"] = {"Authorization": f"Bearer {data['access_token']}"}
    return data


def poll_order_status(client: httpx.Client, headers: dict, order_id: str, target: set[str], timeout: float = 10.0) -> str | None:
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        r = client.get(f"/api/orders/{order_id}", headers=headers)
        if r.status_code == 200:
            last_status = r.json().get("status")
            if last_status in target:
                return last_status
        time.sleep(0.5)
    return last_status


def place_order(client: httpx.Client, headers: dict, symbol: str, side: str, order_type: str, quantity: int, price: str | None = None) -> httpx.Response:
    body = {"symbol": symbol, "side": side, "order_type": order_type, "quantity": quantity}
    if price is not None:
        body["price"] = price
    return client.post("/api/orders", headers=headers, json=body)


def run() -> None:
    print("=== FULL-STACK SMOKE TEST ===")

    with httpx.Client(base_url=API_BASE, timeout=10.0) as client:
        # ---- 1. gateway health ------------------------------------------------
        try:
            r = client.get("/health")
        except httpx.ConnectError:
            print(f"Cannot reach the gateway at {API_BASE} — is `docker compose up` running?")
            sys.exit(2)
        if r.status_code == 200:
            ok("1. GET /health on the gateway -> 200")
        else:
            fail("1. gateway health", f"{r.status_code}")

        # ---- 2. register A and B ----------------------------------------------
        user_a = register(client, "a")
        user_b = register(client, "b")
        if user_a and user_b:
            ok("2. register user A and user B -> 201 + token")
        h_a = user_a["headers"] if user_a else {}
        h_b = user_b["headers"] if user_b else {}

        # ---- 3. duplicate register ---------------------------------------------
        if user_a:
            r = client.post(
                "/api/auth/register",
                json={"username": user_a["user"]["username"], "email": user_a["user"]["email"], "password": "whatever1"},
            )
            (ok if r.status_code == 409 else fail)("3. duplicate register for A -> 409", f"{r.status_code}" if r.status_code != 409 else "")
        else:
            skip("3. duplicate register", "user A was not created")

        # ---- 4. wrong password --------------------------------------------------
        if user_a:
            r = client.post("/api/auth/login", data={"username": user_a["user"]["username"], "password": "not-the-password"})
            (ok if r.status_code == 401 else fail)("4. login with wrong password -> 401", f"{r.status_code}" if r.status_code != 401 else "")
        else:
            skip("4. wrong password login", "user A was not created")

        # ---- 5. unauthenticated /users/me ---------------------------------------
        r = client.get("/api/users/me")
        (ok if r.status_code == 401 else fail)("5. GET /api/users/me with no token -> 401", f"{r.status_code}" if r.status_code != 401 else "")

        # ---- 6. deposit 100000 for A and B ---------------------------------------
        if user_a and user_b:
            ra = client.post("/api/account/deposit", headers=h_a, json={"amount": "100000.00"})
            rb = client.post("/api/account/deposit", headers=h_b, json={"amount": "100000.00"})
            if ra.status_code == 200 and rb.status_code == 200:
                ok("6. deposit 100000 for A and B -> balance reflects it")
            else:
                fail("6. deposit for A/B", f"A={ra.status_code} B={rb.status_code}")
        else:
            skip("6. deposit for A/B", "users not created")

        # ---- 7. invalid deposit ----------------------------------------------------
        if user_a:
            r = client.post("/api/account/deposit", headers=h_a, json={"amount": "-5"})
            (ok if r.status_code == 400 else fail)("7. deposit -5 -> 400", f"{r.status_code}" if r.status_code != 400 else "")
        else:
            skip("7. invalid deposit", "user A was not created")

        # ---- 8. market data ----------------------------------------------------------
        r = client.get("/api/market/symbols")
        if r.status_code == 200 and len(r.json()) == 8:
            ok("8a. GET /api/market/symbols -> 8 symbols")
        else:
            fail("8a. market symbols", f"{r.status_code} len={len(r.json()) if r.status_code == 200 else '?'}")
        r = client.get("/api/market/candles/AAPL", params={"interval": "1m", "limit": 50})
        if r.status_code == 200 and len(r.json()) > 0:
            ok("8b. GET /api/market/candles/AAPL -> non-empty")
        else:
            fail("8b. market candles", f"{r.status_code}")

        # ---- 9. sell without owning shares -> 409 --------------------------------
        if user_b:
            r = place_order(client, h_b, "AAPL", "SELL", "LIMIT", 10, "200.00")
            (ok if r.status_code == 409 else fail)(
                "9. B sells AAPL it does not own -> 409", f"{r.status_code} {r.text}" if r.status_code != 409 else ""
            )
        else:
            skip("9. sell without shares", "user B was not created")

        # ---- 10. give B inventory: seed a counterparty C, cross a trade ----------
        user_c = None
        if user_b:
            user_c = register(client, "c")
            if user_c:
                client.post("/api/account/deposit", headers=user_c["headers"], json={"amount": "100000.00"})
                r_sell = place_order(client, user_c["headers"], "AAPL", "SELL", "LIMIT", 10, "200.00")
                r_buy = place_order(client, h_b, "AAPL", "BUY", "LIMIT", 10, "200.00")
                if r_sell.status_code == 201 and r_buy.status_code == 201:
                    st = poll_order_status(client, h_b, r_buy.json()["id"], {"FILLED", "REJECTED", "CANCELLED"})
                    if st == "FILLED":
                        ok("10. B buys 10 AAPL from a seeded counterparty -> FILLED")
                    else:
                        fail("10. B's seed buy did not fill", f"status={st}")
                else:
                    fail("10. seeding B's inventory", f"sell={r_sell.status_code} buy={r_buy.status_code}")
            else:
                fail("10. seed counterparty C", "could not register")
        else:
            skip("10. give B inventory", "user B was not created")

        # ---- 11/12. B sells to A, poll both to FILLED -----------------------------
        order_a_id = order_b_id = None
        if user_a and user_b:
            r_sell = place_order(client, h_b, "AAPL", "SELL", "LIMIT", 10, "200.00")
            r_buy = place_order(client, h_a, "AAPL", "BUY", "LIMIT", 10, "200.00")
            if r_sell.status_code == 201 and r_buy.status_code == 201:
                order_b_id, order_a_id = r_sell.json()["id"], r_buy.json()["id"]
                ok("11. B places SELL 10 @ 200, A places BUY 10 @ 200")
                st_a = poll_order_status(client, h_a, order_a_id, {"FILLED", "REJECTED", "CANCELLED"})
                st_b = poll_order_status(client, h_b, order_b_id, {"FILLED", "REJECTED", "CANCELLED"})
                if st_a == "FILLED" and st_b == "FILLED":
                    ok("12. both orders reach FILLED within 10s")
                else:
                    fail("12. orders did not both fill", f"A={st_a} B={st_b}")
            else:
                fail("11. placing the crossing orders", f"sell={r_sell.status_code} buy={r_buy.status_code}")
        else:
            skip("11/12. crossing trade", "users not created")

        # ---- 13. A's portfolio ------------------------------------------------------
        if user_a:
            r = client.get("/api/portfolio", headers=h_a)
            if r.status_code == 200:
                holding = next((h for h in r.json().get("holdings", []) if h["symbol"] == "AAPL"), None)
                if holding and int(holding["quantity"]) == 10 and abs(float(holding["avg_cost"]) - 200.0) < 0.01:
                    ok("13. A's portfolio shows 10 AAPL @ avg 200")
                else:
                    fail("13. A's portfolio", f"holding={holding}")
            else:
                fail("13. GET /api/portfolio for A", f"{r.status_code}")
        else:
            skip("13. A's portfolio", "user A was not created")

        # ---- 14. A's balance after the trade -----------------------------------------
        if user_a:
            r = client.get("/api/account/balance", headers=h_a)
            if r.status_code == 200:
                b = r.json()
                if float(b["cash_balance"]) <= 100000 - 2000 + 0.01 and float(b["held_balance"]) == 0.0:
                    ok("14. A's cash is reduced by ~2000, held back to 0")
                else:
                    fail("14. A's balance", str(b))
            else:
                fail("14. GET /api/account/balance for A", f"{r.status_code}")
        else:
            skip("14. A's balance", "user A was not created")

        # ---- 15. A's notifications ------------------------------------------------------
        if user_a:
            r = client.get("/api/notifications", headers=h_a)
            if r.status_code == 200 and any(n["type"].startswith("ORDER_FILLED") or n["type"] == "ORDER_FILLED" for n in r.json()):
                ok("15. A has at least one fill notification")
            else:
                fail("15. A's notifications", f"{r.status_code} {r.text[:200]}")
        else:
            skip("15. A's notifications", "user A was not created")

        # ---- 16. quote + book -------------------------------------------------------------
        r = client.get("/api/market/quote/AAPL")
        (ok if r.status_code == 200 else fail)("16a. GET /api/market/quote/AAPL -> 200", f"{r.status_code}" if r.status_code != 200 else "")
        r = client.get("/api/book/AAPL")
        if r.status_code == 200:
            book = r.json()
            gone = not any(o for o in book.get("bids", []) + book.get("asks", []))
            (ok if gone else fail)("16b. book/AAPL has no resting orders from the filled trade", "" if gone else str(book))
        else:
            fail("16b. GET /api/book/AAPL", f"{r.status_code}")

        # ---- 17/18. place, cancel, cancel again ----------------------------------------
        if user_a:
            r = place_order(client, h_a, "AAPL", "BUY", "LIMIT", 5, "1.00")
            if r.status_code == 201:
                oid = r.json()["id"]
                rc = client.delete(f"/api/orders/{oid}", headers=h_a)
                if rc.status_code == 200:
                    st = poll_order_status(client, h_a, oid, {"CANCELLED"}, timeout=5.0)
                    bal = client.get("/api/account/balance", headers=h_a).json()
                    if st == "CANCELLED" and float(bal["held_balance"]) == 0.0:
                        ok("17. cancel a resting order -> CANCELLED, held back to 0")
                    else:
                        fail("17. cancel", f"status={st} held={bal.get('held_balance')}")
                else:
                    fail("17. DELETE /api/orders/{id}", f"{rc.status_code}")

                rc2 = client.delete(f"/api/orders/{oid}", headers=h_a)
                (ok if rc2.status_code == 409 else fail)("18. cancelling the same order twice -> 409", f"{rc2.status_code}" if rc2.status_code != 409 else "")
            else:
                fail("17. placing the order to cancel", f"{r.status_code}")
        else:
            skip("17/18. cancel flow", "user A was not created")

        # ---- 19. validation ---------------------------------------------------------------
        if user_a:
            r = place_order(client, h_a, "AAPL", "BUY", "LIMIT", 0, "100.00")
            (ok if r.status_code == 400 else fail)("19a. quantity 0 -> 400", f"{r.status_code}" if r.status_code != 400 else "")
            r = place_order(client, h_a, "AAPL", "BUY", "LIMIT", 1, "100.005")
            (ok if r.status_code == 400 else fail)("19b. price 100.005 (sub-cent) -> 400", f"{r.status_code}" if r.status_code != 400 else "")
            r = place_order(client, h_a, "FAKE", "BUY", "LIMIT", 1, "100.00")
            (ok if r.status_code == 400 else fail)("19c. unknown symbol FAKE -> 400", f"{r.status_code}" if r.status_code != 400 else "")
        else:
            skip("19. order validation", "user A was not created")

    # ---- 20. websocket ticks (outside the httpx.Client, needs its own loop) --------------
    if websockets is None:
        skip("20. websocket ticks", "the `websockets` package is not installed")
    elif user_a is None or user_b is None:
        skip("20. websocket ticks", "users not created")
    else:
        import asyncio

        async def watch_ticks() -> bool:
            uri = f"{WS_BASE}/ws/market?symbols=AAPL"
            async with websockets.connect(uri, open_timeout=5) as ws:
                with httpx.Client(base_url=API_BASE, timeout=10.0) as trigger_client:
                    place_order(trigger_client, user_c["headers"] if user_c else h_b, "AAPL", "SELL", "LIMIT", 1, "199.00")
                    place_order(trigger_client, h_a, "AAPL", "BUY", "LIMIT", 1, "199.00")
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    remaining = max(0.1, deadline - time.monotonic())
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    if '"type":"tick"' in msg or '"type": "tick"' in msg:
                        return True
            return False

        try:
            got_tick = asyncio.run(watch_ticks())
        except Exception as exc:  # noqa: BLE001 - report as a failed step, not a crash
            fail("20. websocket ticks", str(exc))
        else:
            (ok if got_tick else fail)("20. a tick arrives on /ws/market within 5s of a trade", "" if got_tick else "no tick received")

    print(f"\n=== SMOKE TEST {'FAILED' if FAILED else 'PASSED'} "
          f"({FAILED} failed, {SKIPPED} skipped) ===")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    run()
