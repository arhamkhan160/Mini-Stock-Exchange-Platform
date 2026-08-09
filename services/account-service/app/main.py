"""Account Service.

Virtual cash: balances, deposits, the transaction ledger, and the funds-hold
primitives Order calls synchronously (`/internal/reservations*`). Settlement
itself is event-driven — see handlers.py — so a slow or down Matching Engine
never blocks a deposit or a balance check.
"""

import hashlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from common.config import settings
from common.events import (
    ORDER_CANCELLED,
    ORDER_REJECTED,
    Q_ACCOUNT_ORDER_CANCELLED,
    Q_ACCOUNT_ORDER_REJECTED,
    Q_ACCOUNT_TRADE_EXECUTED,
    TRADE_EXECUTED,
    Broker,
)
from common.logging_setup import setup_logging

from .deps import SessionLocal, engine, redis
from .handlers import handle_order_cancelled, handle_order_rejected, handle_trade_executed
from .routes import router
from .schemas import HealthOut, ReadyOut

setup_logging(settings.SERVICE_NAME, settings.LOG_LEVEL)
log = logging.getLogger(__name__)


def _secret_fingerprint() -> str:
    return hashlib.sha256(settings.JWT_SECRET.encode()).hexdigest()[:8]


@asynccontextmanager
async def lifespan(app: FastAPI):
    broker = Broker(settings.RABBITMQ_URL, settings.SERVICE_NAME)
    await broker.connect()
    app.state.broker = broker

    await broker.consume(Q_ACCOUNT_TRADE_EXECUTED, [TRADE_EXECUTED], handle_trade_executed)
    await broker.consume(Q_ACCOUNT_ORDER_CANCELLED, [ORDER_CANCELLED], handle_order_cancelled)
    await broker.consume(Q_ACCOUNT_ORDER_REJECTED, [ORDER_REJECTED], handle_order_rejected)

    log.info("account-service ready (jwt fingerprint %s)", _secret_fingerprint())
    yield

    await broker.close()
    await redis.aclose()
    await engine.dispose()


app = FastAPI(
    title="Account Service",
    version="1.0.0",
    description="Virtual cash: balances, deposits, ledger, and funds holds.",
    lifespan=lifespan,
)

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
    return HealthOut(status="ok", service=settings.SERVICE_NAME)


@app.get("/ready", response_model=ReadyOut, tags=["ops"])
async def ready():
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
