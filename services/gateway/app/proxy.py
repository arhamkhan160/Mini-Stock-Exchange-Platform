"""HTTP reverse proxy.

Upstream status codes and bodies are passed through UNTOUCHED. A 409 from the
Account Service must reach the browser as a 409 with its `detail` intact,
otherwise nobody can debug the trading path.
"""

import logging

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from .routing import upstream_name

log = logging.getLogger(__name__)

# Connection-scoped headers must not be forwarded. `content-length` in
# particular: httpx recomputes it, and forwarding a stale value truncates the
# response.
HOP_BY_HOP = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }
)


def _filter(headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


async def forward(
    client: httpx.AsyncClient,
    request: Request,
    upstream_base: str,
    upstream_path: str,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    url = f"{upstream_base}{upstream_path}"
    headers = _filter(request.headers)
    if extra_headers:
        headers.update(extra_headers)

    body = await request.body()

    try:
        upstream = await client.request(
            request.method,
            url,
            content=body or None,
            params=dict(request.query_params),
            headers=headers,
        )
    except httpx.ConnectError:
        name = upstream_name(upstream_base)
        log.warning("upstream %s unreachable", name)
        return JSONResponse({"detail": f"{name} unavailable"}, status_code=503)
    except httpx.TimeoutException:
        name = upstream_name(upstream_base)
        log.warning("upstream %s timed out", name)
        return JSONResponse({"detail": f"{name} timed out"}, status_code=504)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )
