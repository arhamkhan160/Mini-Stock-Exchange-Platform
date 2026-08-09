"""Matching Engine.

No database: the order book lives in memory, by design (proposal §4). A
price-time-priority book is a data-structure problem, and a round trip to
Postgres per fill would be the slowest part of the whole exchange.

ponytail: in-memory book, known ceiling — a restart loses resting orders. The
upgrade path is already here: on boot we ask the Order Service for its open
orders and rebuild the book from them (see `rebuild_book`). If that call fails
we log a warning and start empty; the engine must NEVER fail to boot.
"""

import hashlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from common.config import settings
from common.events import (
    ORDER_ACCEPTED,
    ORDER_CANCEL_REQUESTED,
    Q_MATCHING_CANCEL_REQUESTED,
    Q_MATCHING_ORDER_ACCEPTED,
    Broker,
)
from common.http_client import call_service
from common.logging_setup import setup_logging

from . import handlers
from .engine import books, locks, rest
from .handlers import _parse, _remember, handle_cancel_requested, handle_order_accepted
from .routes import router

setup_logging(settings.SERVICE_NAME, settings.LOG_LEVEL)
log = logging.getLogger(__name__)


def _secret_fingerprint() -> str:
    """First 8 hex chars of SHA-256(JWT_SECRET) — the only cheap way to spot a
    container whose secret drifted from everyone else's."""
    return hashlib.sha256(settings.JWT_SECRET.encode()).hexdigest()[:8]


async def rebuild_book() -> None:
    """Restore resting orders from the Order Service. Never fatal."""
    url = f"{settings.ORDER_SERVICE_URL}/internal/orders/open"
    try:
        payload = await call_service("GET", url, retries=1)
    except Exception as exc:
        log.warning("book rebuild skipped (%s); starting with an empty book", exc)
        return

    restored = 0
    for row in payload.get("orders", []):
        try:
            remaining = int(row["quantity"]) - int(row.get("filled_quantity") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if remaining <= 0:
            continue
        # Rows arrive in created_at order, so appending preserves time priority.
        order = _parse({**row, "quantity": remaining})
        if order is None or order.order_type != "LIMIT":
            continue
        async with locks[order.symbol]:
            rest(books[order.symbol], order)
        _remember(order.order_id)
        restored += 1

    log.info("book rebuilt with %s resting order(s)", restored)


@asynccontextmanager
async def lifespan(app: FastAPI):
    broker = Broker(settings.RABBITMQ_URL, settings.SERVICE_NAME)
    await broker.connect()
    app.state.broker = broker
    handlers.broker = broker

    # Rebuild BEFORE consuming: a live order.accepted arriving mid-rebuild
    # would otherwise be matched against a half-restored book.
    await rebuild_book()

    await broker.consume(Q_MATCHING_ORDER_ACCEPTED, [ORDER_ACCEPTED], handle_order_accepted)
    await broker.consume(Q_MATCHING_CANCEL_REQUESTED, [ORDER_CANCEL_REQUESTED], handle_cancel_requested)

    log.info("matching-engine ready (jwt fingerprint %s)", _secret_fingerprint())
    yield

    handlers.broker = None
    await broker.close()


app = FastAPI(
    title="Matching Engine",
    version="1.0.0",
    description="Price-time-priority limit order book. In-memory by design.",
    lifespan=lifespan,
)

# The gateway is the real entry point; this is for hitting :8004 directly in dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health", tags=["ops"])
async def health():
    """Liveness only — no dependency calls, because Docker polls this."""
    return {"status": "ok", "service": settings.SERVICE_NAME}


@app.get("/ready", tags=["ops"])
async def ready():
    """Readiness. There is no database and no Redis here — the broker is the
    engine's only dependency, so reporting on anything else would be theatre."""
    broker = getattr(app.state, "broker", None)
    broker_state = (
        "ok" if broker is not None and broker.connection is not None and not broker.connection.is_closed
        else "down"
    )
    body = {
        "status": "ok" if broker_state == "ok" else "degraded",
        "service": settings.SERVICE_NAME,
        "database": "n/a",
        "redis": "n/a",
        "broker": broker_state,
        "jwt_secret_fingerprint": _secret_fingerprint(),
    }
    if body["status"] != "ok":
        raise HTTPException(status_code=503, detail=body)
    return body
