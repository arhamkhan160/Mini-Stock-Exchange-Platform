"""API Gateway — the single entry point for the frontend.

Cross-cutting concerns live here so no business service has to repeat them:
routing, JWT validation, Redis-backed rate limiting, request ids, CORS, and the
WebSocket bridge for live market ticks.

It owns no database.
"""

import hashlib
import logging
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from common.config import settings
from common.logging_setup import setup_logging
from common.redis_client import make_redis

from .auth import assert_not_internal, authenticate
from .ratelimit import bucket_for, enforce, limit_for
from .proxy import forward
from .routing import ROUTES, resolve
from .ws_proxy import bridge_market

setup_logging(settings.SERVICE_NAME, settings.LOG_LEVEL)
log = logging.getLogger(__name__)

PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _secret_fingerprint() -> str:
    return hashlib.sha256(settings.JWT_SECRET.encode()).hexdigest()[:8]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # DNS resolution runs in anyio's worker threads, and a lookup for a host
    # that does not exist keeps its thread until the OS resolver gives up —
    # far longer than our connect timeout. With the default pool of 40, a burst
    # aimed at one down service starves resolution for every OTHER service and
    # healthy upstreams start returning 503. Headroom here keeps a dead
    # neighbour from becoming everyone's problem.
    import anyio.to_thread

    anyio.to_thread.current_default_thread_limiter().total_tokens = 200

    # ONE client for the whole process. Creating one per request leaks sockets
    # and exhausts ephemeral ports under the market-maker's load.
    # connect=2s: on a Docker network a reachable service connects in
    # milliseconds, so a longer connect timeout only means every request to a
    # DOWN service stalls for that long. Read stays generous for slow queries.
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=2.0),
        follow_redirects=False,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
    )
    app.state.redis = make_redis(settings.REDIS_URL)
    log.info(
        "gateway ready (jwt fingerprint %s, %d routes)",
        _secret_fingerprint(),
        len(ROUTES),
    )
    yield
    await app.state.client.aclose()
    await app.state.redis.aclose()


app = FastAPI(
    title="API Gateway",
    version="1.0.0",
    description="Single entry point: routing, JWT validation, rate limiting, WebSocket bridge.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "service": settings.SERVICE_NAME}


@app.get("/ready", tags=["ops"])
async def ready():
    redis_state = "down"
    try:
        await app.state.redis.ping()
        redis_state = "ok"
    except Exception:
        log.exception("readiness: redis check failed")
    body = {
        "status": "ok" if redis_state == "ok" else "degraded",
        "service": settings.SERVICE_NAME,
        "redis": redis_state,
        "jwt_secret_fingerprint": _secret_fingerprint(),
        "routes": [prefix for prefix, _ in ROUTES],
    }
    # Rate limiting fails open, so a missing Redis is degraded, not fatal.
    return JSONResponse(body, status_code=200 if redis_state == "ok" else 503)


@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    await bridge_market(websocket)


@app.api_route("/api/{upstream_path:path}", methods=PROXY_METHODS, tags=["proxy"])
async def proxy(request: Request, upstream_path: str) -> Response:
    path = request.url.path

    # 1. /internal/* is never reachable from outside.
    assert_not_internal(path)

    # 2. Preflight never touches an upstream (CORSMiddleware answers it).
    if request.method == "OPTIONS":
        return Response(status_code=200)

    # 3. Authenticate (raises 401 on a protected path without a valid token).
    claims = authenticate(request)

    # 4. Route BEFORE rate limiting, so a typo in a URL does not burn the
    #    caller's quota.
    route = resolve(path)
    if route is None:
        return JSONResponse({"detail": "no route"}, status_code=404)
    upstream_base, forwarded_path = route

    # 5. Rate limit, keyed per user when known, otherwise per client IP.
    identity = claims.get("sub") if claims else (request.client.host if request.client else "anonymous")
    await enforce(
        app.state.redis,
        str(identity),
        limit_for(request.method, path, claims),
        bucket_for(request.method, path),
    )

    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    extra = {"X-Request-Id": request_id}
    if claims:
        extra["X-User-Id"] = str(claims.get("sub", ""))

    response = await forward(app.state.client, request, upstream_base, forwarded_path, extra)
    response.headers["X-Request-Id"] = request_id
    return response
