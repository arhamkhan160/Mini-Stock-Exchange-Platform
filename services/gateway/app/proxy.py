"""HTTP reverse proxy.

Upstream status codes and bodies are passed through UNTOUCHED. A 409 from the
Account Service must reach the browser as a 409 with its `detail` intact,
otherwise nobody can debug the trading path.
"""

import asyncio
import logging

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from .routing import upstream_name

log = logging.getLogger(__name__)

# --- bulkhead -------------------------------------------------------------- #
# Without a per-upstream cap, one DOWN service takes the whole gateway with it:
# every request to it holds a connection slot and a DNS resolver thread until it
# times out, and those pools are SHARED, so calls to healthy services start
# failing too. Measured: 40 concurrent requests to a dead service made a healthy
# one unreachable for ~30s.
#
# Capping in-flight requests per upstream contains the damage to the upstream
# that is actually broken.
MAX_INFLIGHT_PER_UPSTREAM = 8
BULKHEAD_WAIT_SECONDS = 1.0

_bulkheads: dict[str, asyncio.Semaphore] = {}


def _bulkhead(name: str) -> asyncio.Semaphore:
    semaphore = _bulkheads.get(name)
    if semaphore is None:
        semaphore = asyncio.Semaphore(MAX_INFLIGHT_PER_UPSTREAM)
        _bulkheads[name] = semaphore
    return semaphore


# --- circuit breaker ------------------------------------------------------- #
# The bulkhead alone is not enough. A failed DNS lookup keeps its OS thread
# until the resolver gives up, which is far longer than our connect timeout, so
# repeatedly retrying a dead host starves the shared resolver pool and healthy
# services become unreachable too. Measured: a burst at a dead service made a
# healthy one unavailable for ~12s.
#
# After a few consecutive connect failures we stop dialling that upstream at all
# for a short window, which costs no threads and lets everything else through.
# Two kinds of failure, deliberately treated differently:
#
#   HARD (ConnectError)   - DNS said no such host, or the port refused. On a
#                           Docker network that means the container is not
#                           deployed. Nothing to retry; open immediately so we
#                           stop firing doomed lookups at the shared resolver.
#   SOFT (ConnectTimeout) - could be transient congestion, quite possibly caused
#                           by a NEIGHBOURING dead upstream. Needs repeated
#                           failures before we declare a healthy service down,
#                           otherwise one blip locks it out for the whole window.
HARD_FAILURE_THRESHOLD = 1
SOFT_FAILURE_THRESHOLD = 4
BREAKER_OPEN_SECONDS = 10.0

_failures: dict[str, int] = {}
_open_until: dict[str, float] = {}


def _circuit_open(name: str) -> bool:
    until = _open_until.get(name, 0.0)
    if until and asyncio.get_event_loop().time() < until:
        return True
    if until:  # window elapsed: let one probe through (half-open)
        _open_until.pop(name, None)
        _failures[name] = 0
    return False


def _record_failure(name: str, hard: bool) -> None:
    count = _failures.get(name, 0) + 1
    _failures[name] = count
    threshold = HARD_FAILURE_THRESHOLD if hard else SOFT_FAILURE_THRESHOLD
    if count >= threshold and name not in _open_until:
        _open_until[name] = asyncio.get_event_loop().time() + BREAKER_OPEN_SECONDS
        log.warning(
            "circuit opened for %s after %s %s failure(s), failing fast for %.0fs",
            name, count, "hard" if hard else "soft", BREAKER_OPEN_SECONDS,
        )


def _record_success(name: str) -> None:
    if _failures.pop(name, None):
        log.info("circuit closed for %s", name)
    _open_until.pop(name, None)

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

# Headers a CLIENT must never be able to inject. X-Internal-Key is the guard on
# every /internal/ endpoint, and X-User-Id is what services log as the caller:
# accepting either from the outside would let a client forge both.
CLIENT_FORBIDDEN = frozenset({"x-internal-key", "x-user-id"})

# httpx transparently decompresses the upstream body, so a forwarded
# `content-encoding: gzip` would tell the browser to gunzip plain bytes.
UPSTREAM_FORBIDDEN = frozenset({"content-encoding"})


def _from_client(headers) -> dict[str, str]:
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() not in CLIENT_FORBIDDEN
    }


def _from_upstream(headers) -> dict[str, str]:
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() not in UPSTREAM_FORBIDDEN
    }


async def forward(
    client: httpx.AsyncClient,
    request: Request,
    upstream_base: str,
    upstream_path: str,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    # Forward the RAW query string: dict(request.query_params) silently drops
    # all but the last value of a repeated key (?status=NEW&status=FILLED).
    query = request.url.query
    url = f"{upstream_base}{upstream_path}" + (f"?{query}" if query else "")

    headers = _from_client(request.headers)
    if extra_headers:
        headers.update(extra_headers)

    body = await request.body()
    name = upstream_name(upstream_base)

    # Fail fast while the circuit is open: no socket, no DNS lookup, no thread.
    if _circuit_open(name):
        return JSONResponse({"detail": f"{name} unavailable"}, status_code=503)

    semaphore = _bulkhead(name)
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=BULKHEAD_WAIT_SECONDS)
    except (TimeoutError, asyncio.TimeoutError):
        log.warning("upstream %s saturated, shedding load", name)
        return JSONResponse({"detail": f"{name} is busy, retry shortly"}, status_code=503)

    try:
        upstream = await client.request(
            request.method,
            url,
            content=body or None,
            headers=headers,
        )
    # ConnectTimeout is a TimeoutException subclass, so it MUST be caught first.
    # Failing to reach a service at all is "unavailable" (503), not "the
    # gateway waited too long for a reply" (504) — a service that is down and a
    # service that is slow are different problems for whoever is on call.
    except httpx.ConnectError:
        _record_failure(name, hard=True)
        log.warning("upstream %s unreachable", name)
        return JSONResponse({"detail": f"{name} unavailable"}, status_code=503)
    except httpx.ConnectTimeout:
        _record_failure(name, hard=False)
        log.warning("upstream %s connect timed out", name)
        return JSONResponse({"detail": f"{name} unavailable"}, status_code=503)
    except httpx.TimeoutException:
        _record_failure(name, hard=False)
        log.warning("upstream %s timed out", name)
        return JSONResponse({"detail": f"{name} timed out"}, status_code=504)
    finally:
        # Must run on every path, or each upstream permanently wedges after
        # MAX_INFLIGHT_PER_UPSTREAM requests.
        semaphore.release()

    _record_success(name)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_from_upstream(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )
