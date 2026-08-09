"""Gateway authentication and the public-route whitelist.

The gateway validates the JWT and then forwards the `Authorization` header
unchanged — every downstream service validates it again. Defence in depth: a
service is never reachable-but-unprotected if someone bypasses the gateway on
the Docker network.
"""

from fastapi import HTTPException, Request

from common.security import decode_token

# Anything a logged-out visitor must be able to reach. Prefix match.
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/auth/",
    "/api/market/",
    "/api/book/",
)

PUBLIC_EXACT: frozenset[str] = frozenset(
    {"/health", "/ready", "/docs", "/openapi.json", "/redoc", "/ws/market", "/"}
)

# Internal service-to-service endpoints must never be reachable from outside.
FORBIDDEN_SEGMENT = "/internal/"


def is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)


def assert_not_internal(path: str) -> None:
    """`/internal/*` is for in-cluster callers only.

    404 rather than 403 so the outside world cannot even confirm the endpoints
    exist.
    """
    if FORBIDDEN_SEGMENT in path or path.endswith("/internal"):
        raise HTTPException(status_code=404, detail="no route")


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def authenticate(request: Request) -> dict | None:
    """Return JWT claims for a protected path, or None for a public one.

    Raises 401 when a protected path has a missing or invalid token.
    """
    path = request.url.path
    token = bearer_token(request)

    if is_public(path):
        # Still decode when a token is present: it gives us a per-user rate
        # limit identity instead of a shared per-IP one. A bad token on a
        # public route is simply ignored.
        if token:
            try:
                return decode_token(token)
            except HTTPException:
                return None
        return None

    if not token:
        raise HTTPException(
            status_code=401,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(token)
