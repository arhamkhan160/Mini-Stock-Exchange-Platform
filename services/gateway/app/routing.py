"""Path -> upstream resolution.

Rule: strip the `/api` prefix, keep everything after it, keep the query string.
Longest matching prefix wins, so `/api/auth` cannot be shadowed by a shorter
entry added later.
"""

from urllib.parse import urlparse

from common.config import settings

API_PREFIX = "/api"

# (public prefix, upstream base URL)
ROUTES: list[tuple[str, str]] = sorted(
    [
        ("/api/auth", settings.USER_SERVICE_URL),
        ("/api/users", settings.USER_SERVICE_URL),
        ("/api/account", settings.ACCOUNT_SERVICE_URL),
        ("/api/orders", settings.ORDER_SERVICE_URL),
        ("/api/book", settings.MATCHING_ENGINE_URL),
        ("/api/market", settings.MARKET_DATA_SERVICE_URL),
        ("/api/portfolio", settings.PORTFOLIO_SERVICE_URL),
        ("/api/notifications", settings.NOTIFICATION_SERVICE_URL),
    ],
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def resolve(path: str) -> tuple[str, str] | None:
    """Return (upstream_base, upstream_path) or None when no route matches."""
    for prefix, upstream in ROUTES:
        if path == prefix or path.startswith(prefix + "/"):
            return upstream, path[len(API_PREFIX):]
    return None


def upstream_name(base_url: str) -> str:
    """Container name, for readable 503/504 messages."""
    return urlparse(base_url).hostname or base_url
