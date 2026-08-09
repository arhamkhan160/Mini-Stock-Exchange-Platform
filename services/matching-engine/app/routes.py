"""Public order-book REST.

Every read takes the symbol lock. Without it a snapshot can serialise a deque
mid-mutation and hand the UI a book that never existed.
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from common.events import utcnow_iso
from common.money import money_str
from common.security import require_internal_key
from common.symbols import SYMBOLS, normalize_symbol

from .engine import STATS, aggregate, books, locks
from .handlers import seen_orders

log = logging.getLogger(__name__)
router = APIRouter()


def _symbol_or_404(symbol: str) -> str:
    try:
        return normalize_symbol(symbol)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail=f"unknown symbol: {symbol}")


def _snapshot(symbol: str, depth: int) -> dict:
    book = books[symbol]
    best_bid, best_ask = book.best_bid(), book.best_ask()
    return {
        "symbol": symbol,
        "bids": aggregate(book.bids, depth, descending=True),
        "asks": aggregate(book.asks, depth, descending=False),
        "best_bid": money_str(best_bid) if best_bid is not None else None,
        "best_ask": money_str(best_ask) if best_ask is not None else None,
        "spread": money_str(best_ask - best_bid) if best_bid is not None and best_ask is not None else None,
        "ts": utcnow_iso(),
    }


@router.get("/book", tags=["book"], summary="Top of book for every symbol")
async def all_books():
    """Powers the dashboard: one row per symbol, no depth."""
    rows = []
    for symbol in SYMBOLS:
        async with locks[symbol]:
            snap = _snapshot(symbol, depth=1)
        rows.append({
            "symbol": symbol,
            "best_bid": snap["best_bid"],
            "best_ask": snap["best_ask"],
            "spread": snap["spread"],
        })
    return rows


@router.get("/book/{symbol}", tags=["book"], summary="Aggregated depth snapshot")
async def get_book(symbol: str, depth: int = Query(10, ge=1, le=50)):
    """Bids descending, asks ascending, aggregated per price level."""
    sym = _symbol_or_404(symbol)
    async with locks[sym]:
        return _snapshot(sym, depth)


@router.get("/book/{symbol}/trades", tags=["book"], summary="Recent in-memory trades")
async def book_trades(symbol: str, limit: int = Query(50, ge=1, le=200)):
    """Newest first. Bounded to the last 200 trades per symbol by design —
    Market Data owns the durable trade history."""
    sym = _symbol_or_404(symbol)
    async with locks[sym]:
        return list(books[sym].recent_trades)[:limit]


@router.get("/internal/stats", tags=["ops"], dependencies=[Depends(require_internal_key)])
async def stats():
    orders_resting = levels = 0
    for symbol in SYMBOLS:
        async with locks[symbol]:
            book = books[symbol]
            orders_resting += len(book.index)
            levels += book.level_count()
    return {
        "orders_resting": orders_resting,
        "levels": levels,
        "trades_since_start": int(STATS["trades_since_start"]),
        "uptime_s": round(time.monotonic() - STATS["started_at"], 1),
        "orders_seen": len(seen_orders),
    }
