"""User Service.

Identity: registration, login (form + JSON), profile, and the internal lookup
other services use to resolve a user id to an email. No Redis, no broker — the
only dependency is Postgres.
"""

import hashlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from common.config import settings
from common.logging_setup import setup_logging

from .deps import SessionLocal, engine
from .routes import router
from .schemas import HealthOut, ReadyOut

setup_logging(settings.SERVICE_NAME, settings.LOG_LEVEL)
log = logging.getLogger(__name__)


def _secret_fingerprint() -> str:
    """First 8 hex chars of SHA-256(JWT_SECRET) — logged so a misconfigured
    container (wrong secret) is obvious from the logs instead of a wall of 401s."""
    return hashlib.sha256(settings.JWT_SECRET.encode()).hexdigest()[:8]


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("user-service ready (jwt fingerprint %s)", _secret_fingerprint())
    yield
    await engine.dispose()


app = FastAPI(
    title="User Service",
    version="1.0.0",
    description="Registration, authentication, and profile management.",
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
    database = "down"
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        log.exception("readiness: database check failed")

    body = ReadyOut(status="ok" if database == "ok" else "degraded", service=settings.SERVICE_NAME, database=database)
    if body.status != "ok":
        raise HTTPException(status_code=503, detail=body.model_dump())
    return body
