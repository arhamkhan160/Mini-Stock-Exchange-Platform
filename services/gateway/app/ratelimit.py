"""Redis fixed-window rate limiting.

Deliberately fail-open: rate limiting is not a correctness feature, and taking
the whole exchange down because Redis blinked would be a worse outage than the
traffic it was meant to shed.
"""

import logging
import time

from fastapi import HTTPException

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 120          # requests per minute
ORDER_PLACEMENT_LIMIT = 30   # POST /api/orders is the expensive path
BOT_LIMIT = 1200             # seed / market-maker accounts

BOT_EMAIL_SUFFIX = "@mse.local"

_redis_warned_at = 0.0


def limit_for(method: str, path: str, claims: dict | None) -> int:
    """Bot accounts quote continuously during the demo and would 429 themselves."""
    if claims and str(claims.get("email", "")).endswith(BOT_EMAIL_SUFFIX):
        return BOT_LIMIT
    if method == "POST" and path.startswith("/api/orders"):
        return ORDER_PLACEMENT_LIMIT
    return DEFAULT_LIMIT


def bucket_for(method: str, path: str) -> str:
    """Which counter a request is charged to.

    Order placement has its own bucket. Sharing one counter across every path
    meant the frontend's background polling (~70 GETs/min while idle) pushed the
    shared count past ORDER_PLACEMENT_LIMIT within seconds, after which *every*
    order 429'd even though the user had placed none.
    """
    if method == "POST" and path.startswith("/api/orders"):
        return "orders"
    return "default"


async def enforce(redis, identity: str, limit: int, bucket: str = "default") -> None:
    """Raise 429 when `identity` exceeds `limit` for `bucket` within the current minute."""
    global _redis_warned_at

    window = int(time.time() // 60)
    key = f"ratelimit:{identity}:{bucket}:{window}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)  # without this the window never resets
    except Exception:
        now = time.monotonic()
        if now - _redis_warned_at > 60:  # do not spam the log every request
            _redis_warned_at = now
            log.warning("redis unavailable, rate limiting disabled for now")
        return

    if count > limit:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded, slow down",
            headers={"Retry-After": "60"},
        )
