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

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from common.config import settings

log = logging.getLogger(__name__)


def _upstream_url(query_string: str) -> str:
    base = settings.MARKET_DATA_SERVICE_URL.replace("http://", "ws://").replace("https://", "wss://")
    url = f"{base}/ws/market"
    return f"{url}?{query_string}" if query_string else url


async def bridge_market(client_ws: WebSocket) -> None:
    await client_ws.accept()
    query_string = client_ws.scope.get("query_string", b"").decode()
    url = _upstream_url(query_string)

    try:
        async with websockets.connect(url, ping_interval=20, open_timeout=10) as upstream:

            async def client_to_upstream() -> None:
                while True:
                    await upstream.send(await client_ws.receive_text())

            async def upstream_to_client() -> None:
                async for message in upstream:
                    await client_ws.send_text(message)

            tasks = {
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            }
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    except WebSocketDisconnect:
        pass
    except Exception:
        # A failed bridge must CLOSE the client socket, never leave it hanging.
        log.warning("market websocket bridge to %s failed", url, exc_info=True)
    finally:
        with contextlib.suppress(Exception):
            await client_ws.close()
