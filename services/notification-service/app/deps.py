"""Shared singletons: engine, session factory, redis client.

Event handlers open their OWN session from `SessionLocal` — never a
request-scoped one, which would already be closed by the time a message lands.
"""

from common.config import settings
from common.db import make_engine, make_sessionmaker, session_dependency
from common.redis_client import make_redis

SERVICE = "notification"

engine = make_engine(settings.DATABASE_URL)
SessionLocal = make_sessionmaker(engine)
get_session = session_dependency(SessionLocal)

# redis.asyncio connects lazily, so building this at import time is safe.
redis = make_redis(settings.REDIS_URL)
