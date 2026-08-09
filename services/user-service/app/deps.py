"""Shared singletons: engine, session factory.

User Service has exactly one dependency — Postgres. No Redis, no broker: it
neither locks anything nor participates in the event contract.
"""

from common.config import settings
from common.db import make_engine, make_sessionmaker, session_dependency

SERVICE = "user"

engine = make_engine(settings.DATABASE_URL)
SessionLocal = make_sessionmaker(engine)
get_session = session_dependency(SessionLocal)
