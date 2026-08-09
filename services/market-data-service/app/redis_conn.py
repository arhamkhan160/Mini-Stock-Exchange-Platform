"""Shared Redis client for this service.

`redis.asyncio` connects lazily, so a module-level client is ready to import
immediately — no init step, and no `from module import name` binding problem
(rebinding a module global after import does NOT update names already imported
elsewhere).

This mirrors how every other service in the system obtains its client.
"""

from common.config import settings
from common.redis_client import make_redis

redis = make_redis(settings.REDIS_URL)
