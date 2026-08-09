"""User Service.

Identity: registration, login (form + JSON), profile, password change, and the
internal lookups other services use to resolve a user id to an email. No
Redis, no broker — the only dependency is Postgres.
"""

import hashlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from common.config import settings
from common.logging_setup import setup_logging

from .deps import SessionLocal, engine
from .routes_auth import router as auth_router
from .routes_users import router as users_router
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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """The shared contract (§1.6) reserves 422 for nothing — bad input is 400
    everywhere. FastAPI's default is 422; this collapses it to the project's
    `{"detail": "message"}` shape with the first failing field's message."""
    errors = exc.errors()
    msg = errors[0].get("msg", "validation error") if errors else "validation error"
    if msg.startswith("Value error, "):
        msg = msg[len("Value error, "):]
    return JSONResponse(status_code=400, content={"detail": msg})


app.include_router(auth_router)
app.include_router(users_router)


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
