"""Redis: distributed locks, event idempotency, price cache, tick pub/sub.

KEY CATALOG (contract — do not invent new key shapes):
  lock:funds:{user_id}          distributed lock held while reserving cash
  lock:shares:{user_id}:{sym}   distributed lock held while reserving shares
  idem:{service}:{event_id}     event de-duplication marker (TTL 24h)
  md:last_price:{SYMBOL}        latest traded price, plain string "195.5000"
  md:quote:{SYMBOL}             JSON {price, quantity, ts} of the last trade
  ratelimit:{identity}:{minute} gateway fixed-window counter (TTL 60s)
CHANNELS:
  md:ticks                      JSON tick fan-out {symbol, price, quantity, ts}
"""

import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""

TICK_CHANNEL = "md:ticks"


def make_redis(url: str) -> aioredis.Redis:
    return aioredis.from_url(url, encoding="utf-8", decode_responses=True)


class LockTimeout(Exception):
    """Could not acquire the distributed lock within the wait budget."""


@asynccontextmanager
async def distributed_lock(
    redis: aioredis.Redis,
    key: str,
    ttl_ms: int = 5000,
    wait_seconds: float = 5.0,
    retry_seconds: float = 0.05,
):
    """Redlock-lite on a single Redis node.

    The token makes release safe: a lock that already expired and was taken by
    somebody else is NEVER deleted by the previous holder.
    """
    import asyncio
    import uuid

    token = str(uuid.uuid4())
    deadline = asyncio.get_event_loop().time() + wait_seconds
    acquired = False
    while asyncio.get_event_loop().time() < deadline:
        if await redis.set(key, token, nx=True, px=ttl_ms):
            acquired = True
            break
        await asyncio.sleep(retry_seconds)
    if not acquired:
        raise LockTimeout(f"could not acquire lock {key} within {wait_seconds}s")
    try:
        yield token
    finally:
        try:
            await redis.eval(_RELEASE_LUA, 1, key, token)
        except Exception:
            log.exception("failed releasing lock %s (it will expire in %sms)", key, ttl_ms)


async def already_processed(redis: aioredis.Redis, service: str, event_id: str, ttl: int = 86400) -> bool:
    """Idempotency guard. Returns True if this event_id was already handled.

    Call it FIRST in every event handler:
        if await already_processed(r, "portfolio", env["event_id"]): return
    """
    if not event_id:
        return False
    was_set = await redis.set(f"idem:{service}:{event_id}", "1", nx=True, ex=ttl)
    return not bool(was_set)


async def clear_processed(redis: aioredis.Redis, service: str, event_id: str) -> None:
    """Undo the idempotency marker when a handler fails, so the retry can run."""
    if event_id:
        await redis.delete(f"idem:{service}:{event_id}")


async def get_last_price(redis: aioredis.Redis, symbol: str) -> str | None:
    return await redis.get(f"md:last_price:{symbol}")


async def set_last_price(redis: aioredis.Redis, symbol: str, price: str) -> None:
    await redis.set(f"md:last_price:{symbol}", price)
