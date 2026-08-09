"""The in-memory limit order book and the pure matching function.

Nothing in this module does I/O: no database, no broker, no clock beyond
`utcnow_iso`. That is deliberate — `selfcheck.py` exercises the entire matching
algorithm, including every edge case, in about a second without Docker.

Price-time priority:
  * best price first (highest bid / lowest ask),
  * within a price level, FIFO by arrival (a `deque`),
  * the trade price is always the RESTING order's price, so the aggressor gets
    the price improvement. That is why `buy_order_limit_price` rides along on
    `trade.executed`: Account needs it to work out how much it over-reserved.
"""

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from common.events import utcnow_iso
from common.money import money_str
from common.symbols import SYMBOLS

log = logging.getLogger(__name__)

# Left ON for the demo: the invariant checks are cheap and a crossed book is
# the one failure that would silently produce nonsense fills.
DEBUG_ASSERTS = os.getenv("DEBUG_ASSERTS", "1").lower() not in ("0", "false", "no")

MAX_RECENT_TRADES = 200


# `eq=False` is load-bearing: `deque.remove()` compares with `==`, and two
# different orders with identical fields would otherwise be interchangeable —
# cancelling one would silently remove the other.
@dataclass(eq=False)
class BookOrder:
    order_id: str
    user_id: str
    side: str                 # BUY | SELL
    price: Decimal | None     # None for MARKET
    remaining: int
    symbol: str
    seq: int = 0
    order_type: str = "LIMIT"


class OrderBook:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.bids: dict[Decimal, deque[BookOrder]] = {}
        self.asks: dict[Decimal, deque[BookOrder]] = {}
        self.index: dict[str, BookOrder] = {}          # order_id -> order, O(1) cancel
        self.recent_trades: deque[dict] = deque(maxlen=MAX_RECENT_TRADES)  # newest first

    # ponytail: O(levels) best-price scan. Swap for a heap only if a book ever
    # exceeds ~1000 levels; with 8 symbols this beats a heap and cannot be
    # gotten subtly wrong.
    def best_bid(self) -> Decimal | None:
        return max(self.bids) if self.bids else None

    def best_ask(self) -> Decimal | None:
        return min(self.asks) if self.asks else None

    def level_count(self) -> int:
        return len(self.bids) + len(self.asks)


books: dict[str, OrderBook] = {symbol: OrderBook(symbol) for symbol in SYMBOLS}
# One lock per symbol: matching and cancelling the same symbol are serialised,
# different symbols run concurrently.
locks: dict[str, asyncio.Lock] = {symbol: asyncio.Lock() for symbol in SYMBOLS}

STATS: dict[str, float] = {"trades_since_start": 0, "started_at": time.monotonic()}


def make_trade(symbol: str, px: Decimal, qty: int, incoming: BookOrder, resting: BookOrder) -> dict:
    """Build a `trade.executed` payload. Call AFTER both remainders are decremented."""
    buy_o, sell_o = (incoming, resting) if incoming.side == "BUY" else (resting, incoming)
    return {
        "trade_id": str(uuid4()),
        "symbol": symbol,
        "price": money_str(px),
        "quantity": qty,
        "buy_order_id": buy_o.order_id,
        "sell_order_id": sell_o.order_id,
        "buyer_user_id": buy_o.user_id,
        "seller_user_id": sell_o.user_id,
        "aggressor_side": incoming.side,
        "buy_order_remaining": buy_o.remaining,
        "sell_order_remaining": sell_o.remaining,
        "buy_order_limit_price": money_str(buy_o.price) if buy_o.order_type == "LIMIT" else None,
        "executed_at": utcnow_iso(),
    }


def rest(book: OrderBook, order: BookOrder) -> None:
    """Put an order into the book at its limit price, at the BACK of the level."""
    side_map = book.bids if order.side == "BUY" else book.asks
    side_map.setdefault(order.price, deque()).append(order)
    book.index[order.order_id] = order


def match(book: OrderBook, incoming: BookOrder) -> tuple[list[dict], dict | None]:
    """Match `incoming` against the book. Returns (trades, cancel_event).

    Mutates the book and `incoming.remaining`. Does NO I/O — the caller
    publishes the returned events once the book is consistent again.
    """
    original_quantity = incoming.remaining
    trades: list[dict] = []

    opposite = book.asks if incoming.side == "BUY" else book.bids
    price_keys = sorted(opposite) if incoming.side == "BUY" else sorted(opposite, reverse=True)

    for px in price_keys:
        if incoming.remaining == 0:
            break
        if incoming.order_type == "LIMIT":
            if incoming.side == "BUY" and px > incoming.price:
                break
            if incoming.side == "SELL" and px < incoming.price:
                break

        level, skipped = opposite[px], deque()
        while level and incoming.remaining > 0:
            resting = level.popleft()
            if resting.user_id == incoming.user_id:
                # Self-trade prevention: skip, never match. Held aside and put
                # back below, so a level made entirely of this user's orders
                # drains into `skipped` and the loop ends — no spin.
                skipped.append(resting)
                continue

            qty = min(incoming.remaining, resting.remaining)
            incoming.remaining -= qty
            resting.remaining -= qty
            trades.append(make_trade(book.symbol, px, qty, incoming, resting))

            if resting.remaining > 0:
                level.appendleft(resting)   # KEEPS its time priority
            else:
                book.index.pop(resting.order_id, None)

        while skipped:                      # restore, original order, at the front
            level.appendleft(skipped.pop())
        if not level:
            del opposite[px]

    cancel_event = None
    if incoming.remaining > 0:
        if incoming.order_type == "LIMIT":
            rest(book, incoming)            # Good-Till-Cancelled
        else:
            # MARKET is Immediate-Or-Cancel: a remainder is never rested.
            cancel_event = {
                "order_id": incoming.order_id,
                "user_id": incoming.user_id,
                "symbol": book.symbol,
                "side": incoming.side,
                "cancelled_quantity": incoming.remaining,
                "reason": "IOC_REMAINDER" if trades else "NO_LIQUIDITY",
                "cancelled_at": utcnow_iso(),
            }

    for trade in trades:
        book.recent_trades.appendleft(trade)
    STATS["trades_since_start"] += len(trades)

    check_invariants(book, original_quantity, trades, incoming.remaining)
    return trades, cancel_event


def cancel(book: OrderBook, order_id: str) -> BookOrder | None:
    """Remove a resting order. Returns it, or None if the book never held it."""
    order = book.index.pop(order_id, None)
    if order is None:
        return None
    side_map = book.bids if order.side == "BUY" else book.asks
    level = side_map.get(order.price)
    if level is not None:
        try:
            level.remove(order)
        except ValueError:
            pass
        if not level:
            del side_map[order.price]
    return order


def check_invariants(book: OrderBook, original_quantity: int, trades: list[dict], remaining: int) -> None:
    """The two properties that must hold after every match. Loud, never fatal."""
    if not DEBUG_ASSERTS:
        return

    filled = sum(t["quantity"] for t in trades)
    if filled + remaining != original_quantity:
        log.error(
            "QUANTITY CONSERVATION VIOLATED on %s: filled=%s remaining=%s original=%s",
            book.symbol, filled, remaining, original_quantity,
        )

    bid, ask = book.best_bid(), book.best_ask()
    if bid is not None and ask is not None and bid >= ask:
        log.error("CROSSED BOOK on %s: best_bid=%s >= best_ask=%s", book.symbol, bid, ask)


def aggregate(side_map: dict[Decimal, deque[BookOrder]], depth: int, descending: bool) -> list[dict]:
    """Price levels for the REST snapshot: total quantity and order count per price."""
    prices = sorted(side_map, reverse=descending)[:depth]
    return [
        {
            "price": money_str(px),
            "quantity": sum(o.remaining for o in side_map[px]),
            "orders": len(side_map[px]),
        }
        for px in prices
    ]
