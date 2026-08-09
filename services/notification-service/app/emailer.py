"""Mock email delivery.

The proposal asks for "mock email / in-app push". In-app push is the
notifications table; the mock email is a single log line per notification.

The recipient address comes from the User Service, cached in-process. If the
User Service is unreachable we log the user id instead and carry on — a
notification must NEVER fail because an email lookup failed.
"""

import logging
import time

from common.config import settings
from common.http_client import ServiceCallError, call_service

log = logging.getLogger(__name__)

_CACHE_TTL = 300.0
_CACHE_MAX = 1000
_cache: dict[str, tuple[float, str]] = {}


async def resolve_email(user_id: str) -> str:
    """Best-effort address lookup. Returns the user id when unavailable."""
    now = time.monotonic()
    hit = _cache.get(user_id)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    try:
        data = await call_service(
            "GET",
            f"{settings.USER_SERVICE_URL}/internal/users/{user_id}",
            timeout=3.0,
            retries=0,  # a notification must not wait on retries
        )
        email = data.get("email") or user_id
    except (ServiceCallError, Exception):  # noqa: B014 - deliberately catch everything
        log.warning("could not resolve email for user %s, falling back to id", user_id)
        email = user_id

    if len(_cache) >= _CACHE_MAX:
        _cache.clear()  # ponytail: crude eviction, fine for a bounded demo user set
    _cache[user_id] = (now, email)
    return email


async def send_mock_email(user_id: str, title: str, message: str) -> None:
    address = await resolve_email(str(user_id))
    log.info("EMAIL -> %s | %s | %s", address, title, message)
