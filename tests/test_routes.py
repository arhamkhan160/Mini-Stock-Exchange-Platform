"""Route tests — drives the real ASGI apps, no Docker required.

The gateway owns no database, so it runs fully in-process here with stub
upstreams. Every route in the published contract is exercised: correct upstream,
correct path rewrite, correct auth requirement, plus the proxy's error and
header behaviour.

    python tests/test_routes.py

Checks needing a dependency that is not installed are SKIPPED with a reason, so
this runs on a bare machine and inside a container alike.
"""

import asyncio
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "services" / "gateway"))

PUBLIC, JWT = "public", "jwt"

# (method, public path, expected upstream host, expected rewritten path, auth)
ROUTE_TABLE = [
    ("POST",   "/api/auth/register",              "user-service",         "/auth/register",            PUBLIC),
    ("POST",   "/api/auth/login",                 "user-service",         "/auth/login",               PUBLIC),
    ("POST",   "/api/auth/login-json",            "user-service",         "/auth/login-json",          PUBLIC),
    ("GET",    "/api/users/me",                   "user-service",         "/users/me",                 JWT),
    ("PATCH",  "/api/users/me",                   "user-service",         "/users/me",                 JWT),
    ("GET",    "/api/account/balance",            "account-service",      "/account/balance",          JWT),
    ("POST",   "/api/account/deposit",            "account-service",      "/account/deposit",          JWT),
    ("GET",    "/api/account/transactions",       "account-service",      "/account/transactions",     JWT),
    ("POST",   "/api/orders",                     "order-service",        "/orders",                   JWT),
    ("GET",    "/api/orders",                     "order-service",        "/orders",                   JWT),
    ("GET",    "/api/orders/abc-123",             "order-service",        "/orders/abc-123",           JWT),
    ("DELETE", "/api/orders/abc-123",             "order-service",        "/orders/abc-123",           JWT),
    ("GET",    "/api/book/AAPL",                  "matching-engine",      "/book/AAPL",                PUBLIC),
    ("GET",    "/api/market/symbols",             "market-data-service",  "/market/symbols",           PUBLIC),
    ("GET",    "/api/market/quote/AAPL",          "market-data-service",  "/market/quote/AAPL",        PUBLIC),
    ("GET",    "/api/market/candles/AAPL",        "market-data-service",  "/market/candles/AAPL",      PUBLIC),
    ("GET",    "/api/market/trades/AAPL",         "market-data-service",  "/market/trades/AAPL",       PUBLIC),
    ("GET",    "/api/portfolio",                  "portfolio-service",    "/portfolio",                JWT),
    ("GET",    "/api/portfolio/pnl",              "portfolio-service",    "/portfolio/pnl",            JWT),
    ("GET",    "/api/notifications",              "notification-service", "/notifications",            JWT),
    ("GET",    "/api/notifications/unread-count", "notification-service", "/notifications/unread-count", JWT),
    ("POST",   "/api/notifications/abc/read",     "notification-service", "/notifications/abc/read",   JWT),
    ("POST",   "/api/notifications/read-all",     "notification-service", "/notifications/read-all",   JWT),
]


class Skip(Exception):
    """Raised by a check whose dependency is unavailable."""


CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


class FakeRedis:
    """Enough of the redis API for the rate limiter, with no server."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.alive = True

    async def incr(self, key: str) -> int:
        if not self.alive:
            raise ConnectionError("redis down")
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def ping(self) -> bool:
        if not self.alive:
            raise ConnectionError("redis down")
        return True

    async def aclose(self) -> None:
        return None


def build_gateway(handler=None, redis=None):
    """Return (app, client_factory, seen) with stub upstreams wired in."""
    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise Skip(f"needs {exc.name}")
    try:
        from app import main as gateway
    except ModuleNotFoundError as exc:
        raise Skip(f"needs {exc.name} (gateway app could not be imported)")

    # The circuit breaker and bulkhead keep module-level state, so one check
    # tripping a circuit would fast-fail every later check against the same
    # upstream. Each test gets a clean gateway.
    from app import proxy as gateway_proxy

    gateway_proxy._failures.clear()
    gateway_proxy._open_until.clear()
    gateway_proxy._bulkheads.clear()

    seen: list = []

    def default_handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})

    gateway.app.state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler or default_handler)
    )
    gateway.app.state.redis = redis or FakeRedis()
    return gateway.app, httpx, seen


def token_for(email: str = "trader@example.com") -> str:
    from common.security import create_access_token

    return create_access_token(str(uuid.uuid4()), email, "trader")


# --------------------------------------------------------------------------- #
# 1. every documented route reaches the right service at the right path
# --------------------------------------------------------------------------- #
@check
def routes_reach_the_correct_upstream():
    app, httpx, seen = build_gateway()
    auth = {"Authorization": f"Bearer {token_for()}"}

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            for method, path, host, upstream_path, kind in ROUTE_TABLE:
                seen.clear()
                response = await client.request(method, path, headers=auth, json={})
                assert response.status_code == 200, (
                    f"{method} {path} -> {response.status_code} {response.text[:120]}"
                )
                assert len(seen) == 1, f"{method} {path} did not reach exactly one upstream"
                got = seen[0]
                assert got.url.host == host, f"{method} {path} went to {got.url.host}, expected {host}"
                assert got.url.path == upstream_path, (
                    f"{method} {path} forwarded as {got.url.path}, expected {upstream_path}"
                )
                assert got.method == method

    asyncio.run(run())


@check
def protected_routes_require_a_token():
    app, httpx, seen = build_gateway()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            for method, path, _host, _upstream, kind in ROUTE_TABLE:
                seen.clear()
                response = await client.request(method, path, json={})
                if kind == JWT:
                    assert response.status_code == 401, f"{method} {path} allowed an anonymous caller"
                    assert not seen, f"{method} {path} hit the upstream before rejecting"
                else:
                    assert response.status_code == 200, (
                        f"public {method} {path} -> {response.status_code}"
                    )

    asyncio.run(run())


@check
def expired_or_forged_tokens_are_rejected():
    import time
    import warnings

    import jwt as pyjwt

    from common.config import settings

    app, httpx, seen = build_gateway()
    with warnings.catch_warnings():
        # The attacker's key is deliberately short; PyJWT's length warning is noise here.
        warnings.simplefilter("ignore")
        forged = pyjwt.encode(
            {"sub": str(uuid.uuid4()), "exp": time.time() + 60}, "wrong-secret-key-not-ours"
        )
    expired = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "exp": int(time.time()) - 10, "type": "access"},
        settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM,
    )

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            for label, bad in (("forged", forged), ("expired", expired), ("garbage", "not.a.token")):
                seen.clear()
                r = await client.get("/api/portfolio", headers={"Authorization": f"Bearer {bad}"})
                assert r.status_code == 401, f"{label} token was accepted ({r.status_code})"
                assert not seen, f"{label} token reached the upstream"

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# 2. the security boundary
# --------------------------------------------------------------------------- #
@check
def internal_endpoints_are_unreachable_from_outside():
    app, httpx, seen = build_gateway()
    auth = {"Authorization": f"Bearer {token_for()}"}
    attempts = [
        "/api/account/internal/reservations",
        "/api/portfolio/internal/share-reservations",
        "/api/orders/internal/orders/open",
        "/api/users/internal/users/abc",
        "/api/account/internal",
    ]

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            for path in attempts:
                seen.clear()
                r = await client.get(path, headers=auth)
                # 404, not 403: the outside must not be able to confirm these exist.
                assert r.status_code == 404, f"{path} -> {r.status_code}, expected 404"
                assert not seen, f"{path} was proxied to a service"

    asyncio.run(run())


@check
def clients_cannot_forge_identity_headers():
    app, httpx, seen = build_gateway()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            await client.get(
                "/api/portfolio",
                headers={
                    "Authorization": f"Bearer {token_for()}",
                    "X-Internal-Key": "stolen-key",
                    "X-User-Id": "00000000-0000-0000-0000-000000000000",
                },
            )
            forwarded = {k.lower(): v for k, v in seen[-1].headers.items()}
            assert "x-internal-key" not in forwarded, "client X-Internal-Key must be stripped"
            assert forwarded.get("x-user-id") != "00000000-0000-0000-0000-000000000000", (
                "client X-User-Id must be overwritten from verified claims"
            )
            assert "authorization" in forwarded, "the JWT must still reach the service"

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# 3. proxy fidelity
# --------------------------------------------------------------------------- #
@check
def upstream_errors_pass_through_untouched():
    import gzip
    import json as _json

    import httpx as _httpx

    def handler(request):
        # A genuinely gzipped upstream response: httpx decompresses it, so the
        # gateway must NOT forward content-encoding or the browser would try to
        # gunzip plain bytes.
        body = gzip.compress(_json.dumps({"detail": "insufficient buying power"}).encode())
        return _httpx.Response(
            409,
            content=body,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
        )

    app, httpx, _seen = build_gateway(handler)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            r = await client.post(
                "/api/orders", json={}, headers={"Authorization": f"Bearer {token_for()}"}
            )
            assert r.status_code == 409, r.status_code
            assert r.json()["detail"] == "insufficient buying power", r.text
            # httpx already decompressed the body; forwarding this header would
            # make the browser try to gunzip plain bytes.
            assert "content-encoding" not in {k.lower() for k in r.headers}
            assert "x-request-id" in {k.lower() for k in r.headers}

    asyncio.run(run())


@check
def repeated_query_parameters_survive():
    app, httpx, seen = build_gateway()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            await client.get(
                "/api/orders?status=NEW&status=PARTIALLY_FILLED&limit=5",
                headers={"Authorization": f"Bearer {token_for()}"},
            )
            query = seen[-1].url.query.decode()
            assert query.count("status=") == 2, f"repeated key collapsed: {query}"
            assert "limit=5" in query

    asyncio.run(run())


@check
def unreachable_upstream_becomes_503_and_timeout_becomes_504():
    import httpx as _httpx

    def refuse(request):
        raise _httpx.ConnectError("connection refused", request=request)

    def stall(request):
        raise _httpx.ReadTimeout("too slow", request=request)

    async def run():
        for handler, expected in ((refuse, 503), (stall, 504)):
            app, httpx, _ = build_gateway(handler)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
                r = await client.get("/api/market/symbols")
                assert r.status_code == expected, f"expected {expected}, got {r.status_code}"
                assert "detail" in r.json(), r.text

    asyncio.run(run())


@check
def unknown_paths_are_404():
    app, httpx, seen = build_gateway()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            r = await client.get("/api/does-not-exist", headers={"Authorization": f"Bearer {token_for()}"})
            assert r.status_code == 404, r.status_code
            assert not seen, "an unroutable path must not reach any service"

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# 4. rate limiting
# --------------------------------------------------------------------------- #
@check
def order_placement_is_rate_limited_harder_than_reads():
    from app.ratelimit import DEFAULT_LIMIT, ORDER_PLACEMENT_LIMIT

    assert ORDER_PLACEMENT_LIMIT < DEFAULT_LIMIT

    redis = FakeRedis()
    app, httpx, _seen = build_gateway(redis=redis)
    auth = {"Authorization": f"Bearer {token_for()}"}

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            statuses = [
                (await client.post("/api/orders", json={}, headers=auth)).status_code
                for _ in range(ORDER_PLACEMENT_LIMIT + 2)
            ]
            assert statuses[:ORDER_PLACEMENT_LIMIT] == [200] * ORDER_PLACEMENT_LIMIT
            assert statuses[-1] == 429, f"limit never fired: {statuses[-3:]}"

            r = await client.post("/api/orders", json={}, headers=auth)
            assert r.headers.get("retry-after") == "60", "429 must tell the client when to retry"

    asyncio.run(run())


@check
def default_traffic_does_not_consume_the_order_bucket():
    from app.ratelimit import DEFAULT_LIMIT, ORDER_PLACEMENT_LIMIT

    redis = FakeRedis()
    app, httpx, _seen = build_gateway(redis=redis)
    auth = {"Authorization": f"Bearer {token_for()}"}

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            for _ in range(ORDER_PLACEMENT_LIMIT + 1):
                r = await client.get("/api/notifications/unread-count", headers=auth)
                assert r.status_code == 200, f"default bucket tripped early at {r.status_code}"

            r = await client.post("/api/orders", json={}, headers=auth)
            assert r.status_code == 200, (
                "reads and notification polling must not spend the order-placement quota"
            )

            for _ in range(DEFAULT_LIMIT - ORDER_PLACEMENT_LIMIT - 1):
                r = await client.get("/api/notifications/unread-count", headers=auth)
                assert r.status_code == 200, f"default bucket tripped early at {r.status_code}"

            r = await client.get("/api/notifications/unread-count", headers=auth)
            assert r.status_code == 429, "default bucket should still enforce its own limit"

    asyncio.run(run())


@check
def bot_accounts_are_exempt_from_the_order_limit():
    from app.ratelimit import BOT_LIMIT, ORDER_PLACEMENT_LIMIT

    redis = FakeRedis()
    app, httpx, _seen = build_gateway(redis=redis)
    auth = {"Authorization": f"Bearer {token_for('bot1@mse.local')}"}

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            statuses = [
                (await client.post("/api/orders", json={}, headers=auth)).status_code
                for _ in range(ORDER_PLACEMENT_LIMIT + 3)
            ]
            assert 429 not in statuses, "the market maker would throttle itself"
            assert BOT_LIMIT > ORDER_PLACEMENT_LIMIT

    asyncio.run(run())


@check
def rate_limiting_fails_open_when_redis_is_down():
    """Losing Redis must not take the exchange down — it is not a correctness
    feature."""
    redis = FakeRedis()
    redis.alive = False
    app, httpx, seen = build_gateway(redis=redis)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            r = await client.get("/api/market/symbols")
            assert r.status_code == 200, f"request refused with redis down: {r.status_code}"
            assert seen, "request should still have been proxied"

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# 5. ops endpoints
# --------------------------------------------------------------------------- #
@check
def health_answers_without_touching_dependencies():
    redis = FakeRedis()
    redis.alive = False  # health must not care
    app, httpx, _seen = build_gateway(redis=redis)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            r = await client.get("/health")
            assert r.status_code == 200 and r.json()["status"] == "ok", r.text

            # /ready DOES care, and must report degraded rather than lie.
            r = await client.get("/ready")
            assert r.status_code == 503, r.status_code
            assert r.json()["redis"] == "down", r.text

    asyncio.run(run())


@check
def ready_exposes_a_jwt_fingerprint_for_mismatch_hunting():
    app, httpx, _seen = build_gateway()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            body = (await client.get("/ready")).json()
            assert len(body["jwt_secret_fingerprint"]) == 8
            assert set(body["routes"]) == {
                "/api/auth", "/api/users", "/api/account", "/api/orders",
                "/api/book", "/api/market", "/api/portfolio", "/api/notifications",
            }, body["routes"]

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# 6. notification service — auth layer only (its routes need Postgres)
# --------------------------------------------------------------------------- #
NOTIFICATION_SUBPROCESS = r'''
import asyncio, sys, uuid
import httpx
from app.main import app

PROTECTED = [
    ("GET", "/notifications"),
    ("GET", "/notifications/unread-count"),
    ("POST", "/notifications/read-all"),
    ("POST", f"/notifications/{uuid.uuid4()}/read"),
]

async def run():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://svc") as client:
        for method, path in PROTECTED:
            r = await client.request(method, path)
            assert r.status_code == 401, f"{method} {path} -> {r.status_code}, expected 401"
        r = await client.get("/health")
        assert r.status_code == 200 and r.json()["service"], r.text
        # /ready must report degraded rather than claim health it cannot verify.
        r = await client.get("/ready")
        assert r.status_code == 503, f"/ready -> {r.status_code} with no database"

asyncio.run(run())
print("NOTIFICATION_ROUTES_OK")
'''


@check
def notification_routes_reject_anonymous_callers():
    """Runs in a subprocess: every service uses the package name `app`, so two
    of them cannot be imported into one interpreter."""
    import os
    import subprocess

    service_dir = ROOT / "services" / "notification-service"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(service_dir), str(ROOT / "libs")]),
        "LOG_LEVEL": "CRITICAL",
        # The engine is created lazily, so a well-formed URL is enough — these
        # routes reject the caller at the auth layer, before any query.
        "DATABASE_URL": "postgresql+asyncpg://mse:mse_pw@localhost:5439/notification_db",
        "REDIS_URL": "redis://localhost:6379/0",
    }
    result = subprocess.run(
        [sys.executable, "-c", NOTIFICATION_SUBPROCESS],
        cwd=str(service_dir), env=env, capture_output=True, text=True, timeout=120,
    )
    if "ModuleNotFoundError" in result.stderr:
        missing = result.stderr.strip().rsplit("'", 2)[-2] if "'" in result.stderr else "a dependency"
        raise Skip(f"needs {missing}")
    assert "NOTIFICATION_ROUTES_OK" in result.stdout, (
        result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "subprocess failed"
    )


def main() -> int:
    # Importing a service calls setup_logging, which would bury the results
    # under JSON request logs.
    import logging

    logging.disable(logging.CRITICAL)

    passed = failed = skipped = 0
    for fn in CHECKS:
        label = fn.__name__.replace("_", " ")
        try:
            fn()
        except Skip as reason:
            print(f"  SKIP  {label} - {reason}")
            skipped += 1
        except AssertionError as exc:
            print(f"  FAIL  {label}\n        {exc}")
            failed += 1
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"  ERROR {label}\n        {type(exc).__name__}: {exc}")
            failed += 1
        else:
            print(f"  OK    {label}")
            passed += 1

    print(f"\nroutes: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"        ({len(ROUTE_TABLE)} contract routes exercised)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
