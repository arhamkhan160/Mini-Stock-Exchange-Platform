"""Order Service — the saga orchestrator.

It owns the order lifecycle and, more importantly, the compensating actions:
every step that can fail after money has been held knows how to give it back.
See `service.place_order` for the seven-step ordering and `handlers` for the
idempotent consumers.
"""

import hashlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from common.config import settings
from common.events import (
    ORDER_CANCEL_REJECTED,
    ORDER_CANCELLED,
    Q_ORDER_CANCEL_REJECTED,
    Q_ORDER_CANCELLED,
    Q_ORDER_TRADE_EXECUTED,
    TRADE_EXECUTED,
    Broker,
)
from common.logging_setup import setup_logging

from . import deps
from .deps import SessionLocal, engine, redis
from .handlers import (
    handle_cancel_rejected,
    handle_order_cancelled,
    handle_trade_executed,
    startup_reconciliation,
)
from .routes import router
from .schemas import HealthOut, ReadyOut

setup_logging(settings.SERVICE_NAME, settings.LOG_LEVEL)
log = logging.getLogger(__name__)


def _secret_fingerprint() -> str:
    """First 8 hex chars of SHA-256(JWT_SECRET) — the only cheap way to spot a
    container whose secret drifted from everyone else's."""
    return hashlib.sha256(settings.JWT_SECRET.encode()).hexdigest()[:8]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Broker.connect retries 60 x 2s. We do NOT serve POST /orders while it is
    # down only to reject everything — /ready stays 503 until it is up.
    broker = Broker(settings.RABBITMQ_URL, settings.SERVICE_NAME)
    await broker.connect()
    app.state.broker = broker
    deps.broker = broker

    await broker.consume(Q_ORDER_TRADE_EXECUTED, [TRADE_EXECUTED], handle_trade_executed)
    await broker.consume(Q_ORDER_CANCELLED, [ORDER_CANCELLED], handle_order_cancelled)
    await broker.consume(Q_ORDER_CANCEL_REJECTED, [ORDER_CANCEL_REJECTED], handle_cancel_rejected)

    await startup_reconciliation()

    log.info("order-service ready (jwt fingerprint %s)", _secret_fingerprint())
    yield

    deps.broker = None
    await broker.close()
    await redis.aclose()
    await engine.dispose()


app = FastAPI(
    title="Order Service",
    version="1.0.0",
    description="Order lifecycle, the place-order saga and two-phase cancellation.",
    lifespan=lifespan,
)

# The gateway is the real entry point; this is for hitting :8003 directly in dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health", response_model=HealthOut, tags=["ops"])
async def health():
    """Liveness only — no dependency calls, because Docker polls this."""
    return HealthOut(status="ok", service=settings.SERVICE_NAME)


@app.get("/ready", response_model=ReadyOut, tags=["ops"])
async def ready():
    """Readiness — actually touches every dependency."""
    database = redis_state = broker_state = "down"
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        log.exception("readiness: database check failed")
    try:
        await redis.ping()
        redis_state = "ok"
    except Exception:
        log.exception("readiness: redis check failed")

    broker = getattr(app.state, "broker", None)
    if broker is not None and broker.connection is not None and not broker.connection.is_closed:
        broker_state = "ok"

    body = ReadyOut(
        status="ok" if "down" not in (database, redis_state, broker_state) else "degraded",
        service=settings.SERVICE_NAME,
        database=database,
        redis=redis_state,
        broker=broker_state,
        jwt_secret_fingerprint=_secret_fingerprint(),
    )
    if body.status != "ok":
        raise HTTPException(status_code=503, detail=body.model_dump())
    return body
