import asyncio
from typing import Set, Dict, Any
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set["Connection"] = set()

    def add(self, conn: "Connection"):
        self.active_connections.add(conn)

    def remove(self, conn: "Connection"):
        self.active_connections.discard(conn)

    async def broadcast(self, symbol: str, message: dict):
        # iterate over a copy
        for conn in list(self.active_connections):
            if symbol in conn.symbols:
                try:
                    await conn.ws.send_json(message)
                except Exception:
                    self.remove(conn)

MANAGER = ConnectionManager()

class Connection:
    def __init__(self, ws: WebSocket, symbols: Set[str]):
        self.ws = ws
        self.symbols = symbols
