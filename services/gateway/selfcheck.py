"""API Gateway self-check.

Covers the pure routing/auth/limit logic (no network, no Redis), then drives
the real ASGI app with a stub upstream to prove that status codes and error
bodies survive the proxy.

Run:
    # PowerShell, from services/gateway
    $env:PYTHONPATH=".;../../libs"
    python selfcheck.py
"""

import asyncio
import gzip
import json
import logging
import sys
import uuid

import httpx

from common.security import create_access_token

from app.auth import assert_not_internal, is_public
from app.ratelimit import BOT_LIMIT, DEFAULT_LIMIT, ORDER_PLACEMENT_LIMIT, limit_for
from app.routing import resolve

PASSED: list[str] = []


def ok(label: str) -> None:
    PASSED.append(label)
    print(f"  OK  {label}")


def check_routing() -> None:
    base, path = resolve("/api/orders/123")
    assert path == "/orders/123", path
    assert "order-service" in base, base
    ok("/api prefix is stripped and the order service is selected")

    base, path = resolve("/api/market/candles/AAPL")
    assert path == "/market/candles/AAPL" and "market-data-service" in base
    ok("market data routes resolve")

    # /api/notifications must not be shadowed by a shorter prefix.
    base, path = resolve("/api/notifications/unread-count")
    assert "notification-service" in base and path == "/notifications/unread-count"
    ok("longest prefix wins")

    assert resolve("/api/nonsense") is None
    assert resolve("/health") is None
    ok("unknown paths resolve to nothing (404 at the edge)")


def check_public() -> None:
    for path in ("/api/auth/login", "/api/market/symbols", "/api/book/AAPL", "/health", "/ws/market"):
        assert is_public(path), path
    for path in ("/api/orders", "/api/portfolio", "/api/account/balance", "/api/notifications"):
        assert not is_public(path), path
    ok("public whitelist covers auth, market data and the book - nothing else")


def check_internal_blocked() -> None:
    for path in ("/api/account/internal/reservations", "/api/orders/internal/orders/open", "/api/x/internal"):
        try:
            assert_not_internal(path)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 404, exc
        else:
            raise AssertionError(f"{path} should not be reachable from outside")
    assert_not_internal("/api/orders")  # must not raise
    ok("/internal/* is unreachable from outside and answers 404, not 403")


def check_limits() -> None:
    assert limit_for("POST", "/api/orders", None) == ORDER_PLACEMENT_LIMIT
    assert limit_for("GET", "/api/orders", None) == DEFAULT_LIMIT
    assert limit_for("GET", "/api/market/symbols", None) == DEFAULT_LIMIT
    assert limit_for("POST", "/api/orders", {"email": "bot1@mse.local"}) == BOT_LIMIT
    ok("order placement is limited harder, and bot accounts are exempt")


async def check_proxy_passthrough() -> None:
    """A 409 with a `detail` body must survive the proxy unchanged."""
    from app import main as gateway_main

    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # A genuinely gzipped upstream response. httpx decompresses it, so the
        # gateway must NOT forward content-encoding or the browser would try to
        # gunzip plain bytes.
        body = gzip.compress(json.dumps({"detail": "insufficient buying power"}).encode())
        return httpx.Response(
            409,
            content=body,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
        )

    stub = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    class FakeRedis:
        async def incr(self, *_a, **_k):
            return 1

        async def expire(self, *_a, **_k):
            return True

        async def ping(self):
            return True

        async def aclose(self):
            return None

    gateway_main.app.state.client = stub
    gateway_main.app.state.redis = FakeRedis()

    token = create_access_token(str(uuid.uuid4()), "u@example.com", "u")
    transport = httpx.ASGITransport(app=gateway_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/orders", json={"symbol": "AAPL"}, headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 409, r.status_code
        assert r.json()["detail"] == "insufficient buying power", r.text
        assert "x-request-id" in {k.lower() for k in r.headers}, "request id must be returned"
        ok("upstream 409 and its detail reach the client untouched, with a request id")

        assert "content-encoding" not in {k.lower() for k in r.headers}, (
            "content-encoding must be stripped - httpx already decompressed the body"
        )
        ok("content-encoding is not forwarded from the upstream")

        # A client must not be able to forge the internal auth header or the
        # caller identity.
        await client.post(
            "/api/orders",
            json={},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Internal-Key": "stolen-key",
                "X-User-Id": "00000000-0000-0000-0000-000000000000",
            },
        )
        forwarded = {k.lower(): v for k, v in seen[-1].headers.items()}
        assert "x-internal-key" not in forwarded, "client-supplied X-Internal-Key must be stripped"
        assert forwarded.get("x-user-id") != "00000000-0000-0000-0000-000000000000", (
            "client-supplied X-User-Id must be overwritten by the gateway"
        )
        ok("client cannot forge X-Internal-Key or X-User-Id")

        # dict(query_params) would keep only the last value.
        await client.get(
            "/api/orders?status=NEW&status=PARTIALLY_FILLED&limit=5",
            headers={"Authorization": f"Bearer {token}"},
        )
        forwarded_query = str(seen[-1].url.query, "utf-8")
        assert forwarded_query.count("status=") == 2, f"repeated query keys lost: {forwarded_query}"
        assert "limit=5" in forwarded_query
        ok("repeated query parameters survive the proxy")

        r = await client.post("/api/orders", json={})
        assert r.status_code == 401, r.status_code
        ok("protected route without a token is 401")

        r = await client.get("/api/orders/internal/orders/open", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404, r.status_code
        ok("internal path through the proxy is 404")

        r = await client.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"
        ok("/health responds without touching any dependency")

    await stub.aclose()


async def main() -> None:
    logging.disable(logging.CRITICAL)  # importing the app enables JSON request logging
    check_routing()
    check_public()
    check_internal_blocked()
    check_limits()
    await check_proxy_passthrough()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed.")
