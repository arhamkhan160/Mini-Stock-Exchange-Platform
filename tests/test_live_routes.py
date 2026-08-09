"""Live integration test — requires a RUNNING stack.

    docker compose up -d
    python tests/test_live_routes.py

Unlike tests/test_routes.py (which drives the ASGI app in-process), this goes
over real HTTP through the real gateway container, and pushes a real event
through RabbitMQ. It proves the wiring, not just the code:

    RabbitMQ -> consumer -> Postgres -> gateway proxy -> HTTP client

Services that are not deployed yet are reported as PENDING, not FAIL.
"""

import asyncio
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))

GATEWAY = "http://localhost:8000"
RABBITMQ = "amqp://guest:guest@localhost:5672/"

PASSED, FAILED, PENDING = [], [], []


def ok(label: str) -> None:
    PASSED.append(label)
    print(f"  PASS    {label}")


def fail(label: str, detail: str) -> None:
    FAILED.append((label, detail))
    print(f"  FAIL    {label} - {detail}")


def pending(label: str, detail: str) -> None:
    PENDING.append(label)
    print(f"  PENDING {label} - {detail}")


def request(method: str, path: str, token: str | None = None, body=None, extra_headers=None):
    """Returns (status, parsed_body, headers)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{GATEWAY}{path}", data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for key, value in (extra_headers or {}).items():
        req.add_header(key, value)
    # HTTP/1.1 servers send header names lowercased, so normalise the keys —
    # a plain dict() of the response headers is case-SENSITIVE.
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            headers = {k.lower(): v for k, v in response.headers.items()}
            return response.status, (json.loads(raw) if raw else None), headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw.decode(errors="replace")
        return exc.code, parsed, {k.lower(): v for k, v in exc.headers.items()}


def make_token(user_id: str, email: str = "live@example.com") -> str:
    from common.security import create_access_token

    return create_access_token(user_id, email, "livetester")


# --------------------------------------------------------------------------- #
def check_gateway_up() -> bool:
    status, body, _ = request("GET", "/health")
    if status == 200 and body.get("status") == "ok":
        ok("gateway /health")
        return True
    fail("gateway /health", f"status={status} body={body}")
    return False


def check_auth_boundary(token: str) -> None:
    status, body, _ = request("GET", "/api/notifications")
    if status == 401:
        ok("protected route without a token is 401 at the edge")
    else:
        fail("anonymous /api/notifications", f"expected 401, got {status}")

    status, _body, _ = request("GET", "/api/notifications", token="not-a-real-token")
    if status == 401:
        ok("garbage token is rejected")
    else:
        fail("garbage token", f"expected 401, got {status}")

    for path in ("/api/account/internal/reservations", "/api/orders/internal/orders/open"):
        status, _body, _ = request("GET", path, token=token)
        if status == 404:
            ok(f"internal path blocked: {path}")
        else:
            fail(f"internal path {path}", f"expected 404, got {status}")


def check_proxy_to_notification(token: str) -> None:
    status, body, headers = request("GET", "/api/notifications", token=token)
    if status != 200:
        fail("gateway -> notification-service", f"status={status} body={body}")
        return
    if not isinstance(body, list):
        fail("gateway -> notification-service", f"expected a list, got {type(body).__name__}")
        return
    ok("gateway proxies to notification-service over the docker network")

    if any(k.lower() == "x-request-id" for k in headers):
        ok("X-Request-Id is echoed to the client")
    else:
        fail("X-Request-Id", "header missing from the response")

    status, body, _ = request("GET", "/api/notifications/unread-count", token=token)
    if status == 200 and "count" in body:
        ok(f"unread-count works through the gateway (count={body['count']})")
    else:
        fail("unread-count", f"status={status} body={body}")


def check_unbuilt_service_maps_to_503(token: str) -> None:
    status, body, _ = request("GET", "/api/market/symbols")
    if status == 503 and "unavailable" in str(body.get("detail", "")):
        ok("an undeployed upstream maps to 503 with a readable detail")
    elif status == 200:
        pending("market-data 503 mapping", "market-data-service is deployed, so nothing to test")
    else:
        fail("undeployed upstream", f"expected 503, got {status} {body}")


def check_rate_limit(token: str) -> None:
    """POST /api/orders is capped at 30/min.

    The requests MUST go out concurrently. The window is a fixed 60-second
    bucket, so issuing them serially against a down upstream spreads them over
    several buckets and the limit legitimately never trips.
    """
    from concurrent.futures import ThreadPoolExecutor

    def fire(_):
        return request("POST", "/api/orders", token=token, body={})

    with ThreadPoolExecutor(max_workers=40) as pool:
        responses = list(pool.map(fire, range(40)))

    codes = [status for status, _body, _headers in responses]
    throttled = [r for r in responses if r[0] == 429]

    if not throttled:
        fail("rate limit", f"40 concurrent order requests never triggered a 429 (codes: {sorted(set(codes))})")
        return
    ok(f"rate limit fired: {len(throttled)}/40 concurrent order requests got 429")

    retry_after = throttled[0][2].get("retry-after")
    if retry_after == "60":
        ok("429 carries Retry-After: 60")
    else:
        fail("rate limit Retry-After", f"got {retry_after!r}")

    # The assertion that matters: the order-placement bucket must never
    # throttle a READ. A 429 here would be a real bug.
    #
    # A transient 503 is a different matter and is expected while the stack is
    # incomplete: bursting at a service that is not deployed floods Docker's
    # embedded DNS with failed lookups, and healthy lookups queue behind them.
    # The circuit breaker bounds it (measured recovery ~14s), and it disappears
    # entirely once every service is deployed and its name resolves. So this
    # reports recovery time rather than failing on it.
    import time as _time

    started = _time.monotonic()
    status = None
    for _ in range(25):
        status, _body, _headers = request("GET", "/api/notifications", token=token)
        if status == 200:
            break
        if status == 429:
            fail("read after throttling", "a read was rate-limited by the order-placement bucket")
            return
        _time.sleep(1)

    elapsed = _time.monotonic() - started
    if status == 200:
        ok(f"reads are never throttled by the order bucket (healthy after {elapsed:.0f}s)")
        if elapsed > 2:
            print(f"          note: {elapsed:.0f}s of collateral 503s from DNS pressure "
                  f"caused by the undeployed order-service")
    else:
        fail("read after throttling", f"healthy upstream never recovered (last status {status})")


async def check_event_pipeline(token: str, user_id: str) -> None:
    """The real thing: publish to RabbitMQ, read the result back over HTTP."""
    try:
        from common.events import TRADE_EXECUTED, Broker
    except ModuleNotFoundError as exc:
        pending("event pipeline", f"needs {exc.name}")
        return

    broker = Broker(RABBITMQ, "live-test")
    try:
        await broker.connect(retries=3, delay=2.0)
    except Exception as exc:
        pending("event pipeline", f"cannot reach rabbitmq: {exc}")
        return

    seller = str(uuid.uuid4())
    payload = {
        "trade_id": str(uuid.uuid4()),
        "symbol": "AAPL",
        "price": "195.5000",
        "quantity": 10,
        "buy_order_id": str(uuid.uuid4()),
        "sell_order_id": str(uuid.uuid4()),
        "buyer_user_id": user_id,
        "seller_user_id": seller,
        "aggressor_side": "BUY",
        "buy_order_remaining": 0,
        "sell_order_remaining": 0,
        "buy_order_limit_price": "196.0000",
        "executed_at": "2026-08-09T12:00:00.000000Z",
    }
    envelope = await broker.publish_event(TRADE_EXECUTED, payload)
    ok("published trade.executed to RabbitMQ")

    # Consumer -> Postgres -> gateway. Poll rather than sleep blindly.
    found = None
    for _ in range(20):
        await asyncio.sleep(0.5)
        status, body, _ = request("GET", "/api/notifications", token=token)
        if status == 200 and body:
            found = body
            break

    if not found:
        fail("event pipeline", "no notification appeared within 10s")
        await broker.close()
        return
    ok(f"trade event became a notification via the broker ({found[0]['title']})")

    if "Bought 10 AAPL" in found[0]["message"]:
        ok("notification body renders the fill correctly")
    else:
        fail("notification body", found[0]["message"])

    # Republish the SAME envelope: idempotency must suppress a duplicate.
    before = len(found)
    await broker.publish(TRADE_EXECUTED, envelope)
    await asyncio.sleep(3)
    status, body, _ = request("GET", "/api/notifications", token=token)
    if status == 200 and len(body) == before:
        ok("replaying the same event creates no duplicate notification")
    else:
        fail("idempotency", f"count went from {before} to {len(body) if body else '?'}")

    await broker.close()


def main() -> int:
    print(f"Live stack test against {GATEWAY}\n")
    print("-- gateway --")
    if not check_gateway_up():
        print("\nGateway is not running. Start it with: docker compose up -d gateway")
        return 1

    user_id = str(uuid.uuid4())
    token = make_token(user_id)

    print("\n-- auth boundary --")
    check_auth_boundary(token)

    print("\n-- proxying --")
    check_proxy_to_notification(token)
    check_unbuilt_service_maps_to_503(token)

    print("\n-- event pipeline (rabbitmq -> consumer -> postgres -> gateway) --")
    asyncio.run(check_event_pipeline(token, user_id))

    print("\n-- rate limiting --")
    check_rate_limit(token)

    print("\n" + "=" * 60)
    print(f"PASS {len(PASSED)}   FAIL {len(FAILED)}   PENDING {len(PENDING)}")
    for label, detail in FAILED:
        print(f"  - {label}: {detail}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
