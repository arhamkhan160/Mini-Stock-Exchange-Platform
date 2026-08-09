"""WebSocket bridge.

The proposal requires the gateway to be the single entry point, so live ticks
are proxied rather than exposing the Market Data service directly. Two pump
tasks run concurrently; the first to finish cancels the other.

If this ever misbehaves during the demo, the frontend can point straight at
market-data by changing NEXT_PUBLIC_WS_URL — one env var, no code change.
"""

import asyncio
import contextlib
import logging
from collections import Counter

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from common.config import settings

log = logging.getLogger(__name__)

# The tick feed is public and unauthenticated, so it needs its own ceiling —
# the HTTP rate limiter never sees these connections. A runaway reconnect loop
# in one browser tab must not be able to exhaust the process.
MAX_TOTAL_CONNECTIONS = 200
MAX_PER_CLIENT = 5

_per_client: Counter[str] = Counter()


def _client_key(ws: WebSocket) -> str:
    return ws.client.host if ws.client else "unknown"


def _upstream_url(query_string: str) -> str:
    base = settings.MARKET_DATA_SERVICE_URL.replace("http://", "ws://").replace("https://", "wss://")
    url = f"{base}/ws/market"
    return f"{url}?{query_string}" if query_string else url


async def bridge_market(client_ws: WebSocket) -> None:
    key = _client_key(client_ws)
    if sum(_per_client.values()) >= MAX_TOTAL_CONNECTIONS or _per_client[key] >= MAX_PER_CLIENT:
        log.warning("refusing market websocket from %s (at capacity)", key)
        await client_ws.close(code=1013)  # 1013 = try again later
        return

    await client_ws.accept()
    _per_client[key] += 1
    query_string = client_ws.scope.get("query_string", b"").decode()
    url = _upstream_url(query_string)

    try:
        async with websockets.connect(url, ping_interval=20, open_timeout=10) as upstream:

            async def client_to_upstream() -> None:
                while True:
                    try:
                        message = await client_ws.receive_text()
                    except WebSocketDisconnect:
                        return
                    try:
                        await upstream.send(message)
                    except ConnectionClosed:
                        return

            async def upstream_to_client() -> None:
                try:
                    async for message in upstream:
                        await client_ws.send_text(message)
                except (ConnectionClosed, WebSocketDisconnect, RuntimeError):
                    return

            tasks = {
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)

    except WebSocketDisconnect:
        pass
    except Exception:
        # A failed bridge must CLOSE the client socket, never leave it hanging.
        log.warning("market websocket bridge to %s failed", url, exc_info=True)
    finally:
        _per_client[key] -= 1
        if _per_client[key] <= 0:
            del _per_client[key]  # keep the counter from growing per unique IP
        with contextlib.suppress(Exception):
            await client_ws.close()
