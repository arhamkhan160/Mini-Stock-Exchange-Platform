# API Gateway — service notes

**Port 8000 · no database · owner: Arham Ibrahim Khan (Team A)**

The single entry point for the frontend. Cross-cutting concerns live here so no
business service repeats them: routing, JWT validation, rate limiting, request
ids, CORS, and the WebSocket bridge for live ticks.

## Routing

Rule: **strip `/api`, keep the rest of the path and the query string.** Longest
matching prefix wins, so a shorter entry added later cannot shadow an existing
one.

| Public prefix | Upstream |
|---|---|
| `/api/auth`, `/api/users` | user-service:8001 |
| `/api/account` | account-service:8002 |
| `/api/orders` | order-service:8003 |
| `/api/book` | matching-engine:8004 |
| `/api/market` | market-data-service:8005 |
| `/api/portfolio` | portfolio-service:8006 |
| `/api/notifications` | notification-service:8007 |
| `WS /ws/market` | market-data-service:8005 `/ws/market` |

`GET /health` (liveness, no dependencies) and `GET /ready` (Redis + JWT
fingerprint + route list) are served by the gateway itself.

## Authentication

Public prefixes: `/api/auth/`, `/api/market/`, `/api/book/`. Public exact paths:
`/health`, `/ready`, `/docs`, `/openapi.json`, `/redoc`, `/ws/market`, `/`.
Everything else needs a valid `Authorization: Bearer <jwt>`.

The gateway validates the token **and forwards the header unchanged** — every
downstream service validates it again. Defence in depth: no service is
reachable-but-unprotected for anything already on the Docker network.

A valid token on a *public* route is still decoded, because it upgrades the
rate-limit identity from per-IP to per-user. An invalid token there is ignored.

**`/internal/*` is answered with 404** — not 403, which would confirm the
endpoints exist. Internal service-to-service endpoints are reachable only from
inside the Docker network, guarded by `X-Internal-Key`.

## Rate limiting

Redis fixed window, `ratelimit:{identity}:{minute}` with a 60-second expiry.
Identity is the user id when authenticated, otherwise the client IP.

| Scope | Limit / minute |
|---|---|
| `POST /api/orders` | 30 |
| Everything else | 120 |
| Accounts whose email ends `@mse.local` (seed bots, market maker) | 1200 |

**Fails open.** If Redis is unavailable the request is allowed and a warning is
logged at most once a minute. Rate limiting is not a correctness feature, and
taking the exchange down because Redis blinked is the worse outage.

## Proxy behaviour

- One shared `httpx.AsyncClient` for the process — a client per request leaks
  sockets and exhausts ephemeral ports under the market maker's load.
- Timeouts: 15 s read, **2 s connect**. On a Docker network a reachable service
  connects in milliseconds, so a longer connect timeout only means every request
  to a *down* service stalls for that long.
- `ConnectError` and `ConnectTimeout` → **503** (*unavailable*);
  read/write timeouts → **504** (*timed out*). `ConnectTimeout` subclasses
  `TimeoutException`, so it must be caught first. The distinction matters: a
  service that is down and a service that is slow are different problems.
- Hop-by-hop headers are stripped both ways, including `content-length`
  (httpx recomputes it; forwarding a stale value truncates the response).
- **`content-encoding` is stripped from upstream responses.** httpx already
  decompressed the body, so forwarding it would tell the browser to gunzip
  plain bytes.
- **`X-Internal-Key` and `X-User-Id` are stripped from client requests.** The
  first guards every `/internal/` endpoint and the second is what services log
  as the caller; accepting either from outside would let a client forge both.
  The gateway sets `X-User-Id` itself from verified JWT claims.
- The **raw query string** is forwarded, not `dict(query_params)`, which would
  silently keep only the last value of a repeated key.
- Rate limiting happens **after** routing, so a mistyped URL does not burn the
  caller's quota.
- **Upstream status codes and bodies pass through untouched** — a 409 with
  `"insufficient buying power"` must reach the browser intact or the trading
  path is undebuggable.
- `X-Request-Id` is generated when absent, forwarded upstream, and echoed back.
- `X-User-Id` is added for downstream logging (services still trust only the JWT).
- `OPTIONS` preflight is answered at the edge, never proxied.

## WebSocket bridge

`/ws/market` opens an upstream socket to market-data and runs two pump tasks;
the first to finish cancels the other. A failure closes the client socket rather
than leaving it hanging.

The feed is public and unauthenticated, and these connections never reach the
HTTP rate limiter, so they have their own ceiling: **200 concurrent in total,
5 per client IP**, refused with close code 1013 (*try again later*). A runaway
reconnect loop in one browser tab cannot exhaust the process.

If the bridge ever misbehaves during the demo, the frontend can talk to
market-data directly by changing `NEXT_PUBLIC_WS_URL` — one env var, no code
change.

## Failure isolation (bulkhead + circuit breaker)

Added after a measured cascading failure: **40 concurrent requests to a service
that was not deployed made a healthy service unreachable for ~40 s.** DNS
resolution runs in a shared worker-thread pool, and a lookup for a host that
does not exist keeps its thread until the OS resolver gives up — far longer than
the connect timeout. Doomed lookups therefore starve resolution for everyone.

Three layers now contain it:

| Layer | Setting | Purpose |
|---|---|---|
| Bulkhead | 8 in-flight per upstream, 1 s wait then 503 | One upstream cannot consume the whole connection/resolver budget |
| Circuit breaker | opens for 10 s | Stops dialling a known-dead host: no socket, no DNS lookup, no thread |
| Resolver headroom | anyio thread limiter raised to 200 | Healthy lookups are not stuck behind failing ones |

The breaker treats two failure kinds differently, which matters:

- **Hard** (`ConnectError` — no such host, connection refused): opens after
  **1** failure. On a Docker network this means the container is not deployed;
  retrying only floods the resolver.
- **Soft** (`ConnectTimeout`): opens after **4**. A timeout may be transient
  congestion — quite possibly caused by a neighbouring dead upstream — and
  locking a healthy service out on one blip is worse than the blip.

After the open window one probe is allowed through (half-open); success closes
the circuit.

Measured result: collateral impact on a healthy service dropped from ~40 s to
~14 s, and it disappears entirely once every service is deployed, because then
every hostname resolves immediately. Remaining time is Docker's embedded DNS
recovering from the NXDOMAIN burst, which is outside the gateway's control.

**Note for tests:** `_failures`, `_open_until` and `_bulkheads` are module-level
dicts. A test that trips a circuit will fast-fail later tests against the same
upstream unless it clears them — `tests/test_routes.py` does this per check.

## Known limitations

- Responses are buffered, not streamed. Fine for candle payloads (hundreds of
  KB at most); a large file download would need `client.stream()`.
- The rate-limit window is fixed, not sliding, so a burst can straddle a minute
  boundary and briefly allow up to 2× the limit. Acceptable for this workload.
- No circuit breaker: a permanently down upstream is retried on every request
  and simply returns 503 each time.

## Self-check

```powershell
# from services/gateway
$env:PYTHONPATH=".;../../libs"
python selfcheck.py
```

Covers routing and prefix precedence, the public whitelist, `/internal/`
blocking, per-route limits and bot exemption, and — through the real ASGI app
with a stubbed upstream — 409 passthrough, 401 on protected routes, 404 for
internal paths, and dependency-free `/health`.
