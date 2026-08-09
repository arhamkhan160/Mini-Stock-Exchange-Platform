#!/usr/bin/env python3
"""Matching engine self-check — pure asserts against `match()`.

No broker, no network, no database, no Docker. Runs in about a second and is
the single most valuable test in the project: if this is green the exchange
prices things correctly, and if it is red nothing else matters.

    python services/matching-engine/selfcheck.py
"""

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "libs"), str(Path(__file__).resolve().parent)]

from app.engine import BookOrder, OrderBook, cancel, match  # noqa: E402

_seq = 0
FAILURES: list[str] = []


def order(side, qty, price=None, user="u1", otype="LIMIT", symbol="AAPL") -> BookOrder:
    global _seq
    _seq += 1
    return BookOrder(
        order_id=f"o{_seq}",
        user_id=user,
        side=side,
        price=None if price is None else Decimal(str(price)),
        remaining=qty,
        symbol=symbol,
        seq=_seq,
        order_type=otype,
    )


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  {detail}")


def submit(book: OrderBook, o: BookOrder, case: str, allow_crossed: bool = False):
    """match() + the two invariants that must hold after EVERY match.

    `allow_crossed` is only for the self-trade cases: skipping a match on
    purpose is the one legitimate way to leave bid >= ask.
    """
    original = o.remaining
    trades, cancel_event = match(book, o)

    filled = sum(t["quantity"] for t in trades)
    check(
        f"[{case}] quantity conservation",
        filled + o.remaining == original,
        f"filled={filled} remaining={o.remaining} original={original}",
    )
    if not allow_crossed:
        bid, ask = book.best_bid(), book.best_ask()
        check(
            f"[{case}] book not crossed",
            bid is None or ask is None or bid < ask,
            f"best_bid={bid} best_ask={ask}",
        )
    return trades, cancel_event


# --------------------------------------------------------------------------- #
print("\nmatching engine selfcheck")
print("=" * 60)

# 1. empty book + LIMIT BUY 10@100 -> rests
book = OrderBook("AAPL")
trades, ce = submit(book, order("BUY", 10, 100), "rest-limit")
check("empty book: LIMIT BUY rests, no trades", trades == [] and ce is None, str(trades))
check("empty book: one bid level", len(book.bids) == 1 and len(book.asks) == 0, str(book.bids))

# 2. full match
trades, ce = submit(book, order("SELL", 10, 100, user="u2"), "full-match")
check("full match: exactly one trade", len(trades) == 1, str(trades))
check("full match: price 100", trades[0]["price"] == "100.0000", trades[0]["price"])
check("full match: both remainders 0",
      trades[0]["buy_order_remaining"] == 0 and trades[0]["sell_order_remaining"] == 0, str(trades[0]))
check("full match: book empty", not book.bids and not book.asks and not book.index, str(book.index))

# 3. partial fill of a resting order
book = OrderBook("AAPL")
buy = order("BUY", 10, 100)
submit(book, buy, "partial-setup")
trades, ce = submit(book, order("SELL", 4, 100, user="u2"), "partial")
check("partial: one trade of 4", len(trades) == 1 and trades[0]["quantity"] == 4, str(trades))
check("partial: buy_order_remaining 6", trades[0]["buy_order_remaining"] == 6, str(trades[0]))
check("partial: resting bid keeps 6", buy.remaining == 6 and book.index.get(buy.order_id) is buy, str(buy))
check("partial: seller fully filled", trades[0]["sell_order_remaining"] == 0, str(trades[0]))

# 4. price improvement — the aggressor pays the RESTING price
book = OrderBook("AAPL")
submit(book, order("SELL", 10, 100, user="u2"), "improve-setup")
trades, ce = submit(book, order("BUY", 10, 105, user="u1"), "improve")
check("price improvement: trades at 100 not 105", trades[0]["price"] == "100.0000", trades[0]["price"])
check("price improvement: buy_order_limit_price is the buyer's 105",
      trades[0]["buy_order_limit_price"] == "105.0000", str(trades[0]))

# 5. time priority within a level
book = OrderBook("AAPL")
first = order("BUY", 5, 100, user="uA")
second = order("BUY", 5, 100, user="uB")
submit(book, first, "time-setup-a")
submit(book, second, "time-setup-b")
trades, ce = submit(book, order("SELL", 5, 100, user="uZ"), "time-priority")
check("time priority: the FIRST order fills", trades[0]["buy_order_id"] == first.order_id,
      f"filled {trades[0]['buy_order_id']}, expected {first.order_id}")
check("time priority: the second order is untouched",
      second.remaining == 5 and second.order_id in book.index, str(second))

# 6. price priority across levels
book = OrderBook("AAPL")
low = order("BUY", 5, 100, user="uA")
high = order("BUY", 5, 101, user="uB")
submit(book, low, "price-setup-a")
submit(book, high, "price-setup-b")
trades, ce = submit(book, order("SELL", 5, None, user="uZ", otype="MARKET"), "price-priority")
check("price priority: the 101 bid fills first", trades[0]["price"] == "101.0000", trades[0]["price"])
check("price priority: the 100 bid is untouched", low.remaining == 5, str(low))
check("price priority: MARKET sell has no limit price on the buy leg",
      trades[0]["buy_order_limit_price"] == "101.0000", str(trades[0]))

# 7. self-trade prevention — both sides rest, nothing matches
book = OrderBook("AAPL")
submit(book, order("BUY", 5, 100, user="same"), "self-setup")
trades, ce = submit(book, order("SELL", 5, 100, user="same"), "self-trade", allow_crossed=True)
check("self-trade: no trades", trades == [], str(trades))
check("self-trade: both orders rest", len(book.index) == 2, str(book.index))
check("self-trade: book IS crossed here by design (both sides same user)",
      book.best_bid() == book.best_ask(), f"{book.best_bid()} vs {book.best_ask()}")

# 8. a level made entirely of the same user's orders — the infinite-loop trap
book = OrderBook("AAPL")
mine = order("BUY", 5, 100, user="solo")
match(book, mine)
trades, ce = match(book, order("SELL", 5, None, user="solo", otype="MARKET"))
check("self-only level: no trades", trades == [], str(trades))
check("self-only level: NO_LIQUIDITY cancel", ce is not None and ce["reason"] == "NO_LIQUIDITY", str(ce))
check("self-only level: cancelled_quantity 5", ce and ce["cancelled_quantity"] == 5, str(ce))
check("self-only level: the resting bid survived, in order",
      list(book.bids[Decimal("100")]) == [mine], str(book.bids))

# 9. MARKET order sweeping several levels
book = OrderBook("AAPL")
for px in (100, 101, 102):
    submit(book, order("SELL", 5, px, user="uM"), f"sweep-setup-{px}")
trades, ce = submit(book, order("BUY", 12, None, user="uZ", otype="MARKET"), "sweep")
check("sweep: three trades", len(trades) == 3, str(len(trades)))
check("sweep: quantities 5,5,2", [t["quantity"] for t in trades] == [5, 5, 2],
      str([t["quantity"] for t in trades]))
check("sweep: prices 100,101,102", [t["price"] for t in trades] == ["100.0000", "101.0000", "102.0000"],
      str([t["price"] for t in trades]))
check("sweep: running buy remainders 7,2,0", [t["buy_order_remaining"] for t in trades] == [7, 2, 0],
      str([t["buy_order_remaining"] for t in trades]))
check("sweep: no cancel event, fully filled", ce is None, str(ce))
check("sweep: 102 level partially left with 3", book.asks[Decimal("102")][0].remaining == 3, str(book.asks))

# 10. MARKET into an empty book
book = OrderBook("AAPL")
trades, ce = submit(book, order("BUY", 5, None, user="uZ", otype="MARKET"), "no-liquidity")
check("no liquidity: no trades", trades == [], str(trades))
check("no liquidity: NO_LIQUIDITY, qty 5",
      ce is not None and ce["reason"] == "NO_LIQUIDITY" and ce["cancelled_quantity"] == 5, str(ce))
check("no liquidity: nothing rested", not book.bids and not book.index, str(book.index))

# 11. IOC remainder — partially filled MARKET order
book = OrderBook("AAPL")
submit(book, order("SELL", 3, 100, user="uM"), "ioc-setup")
trades, ce = submit(book, order("BUY", 10, None, user="uZ", otype="MARKET"), "ioc")
check("ioc: filled 3", len(trades) == 1 and trades[0]["quantity"] == 3, str(trades))
check("ioc: IOC_REMAINDER for the other 7",
      ce is not None and ce["reason"] == "IOC_REMAINDER" and ce["cancelled_quantity"] == 7, str(ce))
check("ioc: MARKET remainder never rests", not book.bids, str(book.bids))

# 12. cancel a resting order
book = OrderBook("AAPL")
resting = order("BUY", 10, 100)
submit(book, resting, "cancel-setup")
removed = cancel(book, resting.order_id)
check("cancel: returns the order with remaining 10", removed is not None and removed.remaining == 10, str(removed))
check("cancel: index empty", not book.index, str(book.index))
check("cancel: empty price level removed", not book.bids, str(book.bids))

# 13. cancel an order the book never held
check("cancel unknown: returns None (=> NOT_IN_BOOK)", cancel(book, "does-not-exist") is None)

# 14. partially filled then cancelled — the remainder is what gets released
book = OrderBook("AAPL")
big = order("BUY", 10, 100, user="uA")
submit(book, big, "part-cancel-setup")
submit(book, order("SELL", 4, 100, user="uB"), "part-cancel-fill")
removed = cancel(book, big.order_id)
check("partial then cancel: cancelled_quantity is 6, not 10",
      removed is not None and removed.remaining == 6, str(removed))

# 15. tick normalisation — 100.10 and 100.1 must be ONE level
book = OrderBook("AAPL")
submit(book, order("BUY", 5, "100.10", user="uA"), "tick-a")
submit(book, order("BUY", 5, "100.1", user="uB"), "tick-b")
check("decimal keys: 100.10 and 100.1 are one level", len(book.bids) == 1, str(list(book.bids)))

# --------------------------------------------------------------------------- #
# 16. EVENT CONTRACT (§1.7). Five other services are coded against these exact
#     field names, so a typo here is a system-wide outage, not a local bug.
# --------------------------------------------------------------------------- #
import asyncio  # noqa: E402

from app import handlers  # noqa: E402
from app.engine import books  # noqa: E402


class FakeBroker:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    async def publish_event(self, routing_key, payload):
        self.published.append((routing_key, payload))


async def contract_checks() -> None:
    fake = FakeBroker()
    handlers.broker = fake
    for symbol in books:
        books[symbol].bids.clear()
        books[symbol].asks.clear()
        books[symbol].index.clear()
    handlers.seen_orders.clear()

    accepted = {
        "order_id": "11111111-1111-1111-1111-111111111111",
        "user_id": "aaaaaaaa-1111-1111-1111-111111111111",
        "symbol": "AAPL", "side": "SELL", "order_type": "LIMIT",
        "price": "100.00", "quantity": 10, "created_at": "2026-08-10T00:00:00.000000Z",
    }
    await handlers.handle_order_accepted({"event_id": "e1", "payload": accepted})
    check("consumer: a resting LIMIT publishes nothing", fake.published == [], str(fake.published))

    taker = {**accepted, "order_id": "22222222-2222-2222-2222-222222222222",
             "user_id": "bbbbbbbb-2222-2222-2222-222222222222",
             "side": "BUY", "order_type": "MARKET", "price": None, "quantity": 12}
    await handlers.handle_order_accepted({"event_id": "e2", "payload": taker})

    keys = [k for k, _ in fake.published]
    check("consumer: MARKET taker emits trade.executed then order.cancelled",
          keys == ["trade.executed", "order.cancelled"], str(keys))

    trade = fake.published[0][1]
    check("trade.executed fields exact", set(trade) == {
        "trade_id", "symbol", "price", "quantity", "buy_order_id", "sell_order_id",
        "buyer_user_id", "seller_user_id", "aggressor_side", "buy_order_remaining",
        "sell_order_remaining", "buy_order_limit_price", "executed_at",
    }, str(sorted(trade)))
    check("trade.executed: MARKET buyer has a null limit price",
          trade["buy_order_limit_price"] is None, str(trade["buy_order_limit_price"]))
    check("trade.executed: price is a 4dp string", trade["price"] == "100.0000", trade["price"])

    cancelled = fake.published[1][1]
    check("order.cancelled fields exact", set(cancelled) == {
        "order_id", "user_id", "symbol", "side", "cancelled_quantity", "reason", "cancelled_at",
    }, str(sorted(cancelled)))
    check("order.cancelled: IOC_REMAINDER of 2",
          cancelled["reason"] == "IOC_REMAINDER" and cancelled["cancelled_quantity"] == 2, str(cancelled))

    # duplicate delivery of the SAME order.accepted must change nothing
    before = len(fake.published)
    await handlers.handle_order_accepted({"event_id": "e2-again", "payload": taker})
    check("consumer: duplicate order.accepted is ignored", len(fake.published) == before,
          str(fake.published[before:]))

    # cancel an order the book no longer holds -> ALREADY_FILLED, never cancelled
    fake.published.clear()
    await handlers.handle_cancel_requested({"event_id": "e3", "payload": {
        "order_id": taker["order_id"], "user_id": taker["user_id"],
        "symbol": "AAPL", "side": "BUY", "requested_at": "2026-08-10T00:00:00.000000Z",
    }})
    key, rejected = fake.published[0]
    check("cancel of a filled order -> cancel_rejected", key == "order.cancel_rejected", key)
    check("order.cancel_rejected fields exact",
          set(rejected) == {"order_id", "user_id", "reason", "rejected_at"}, str(sorted(rejected)))
    check("cancel of a filled order -> ALREADY_FILLED", rejected["reason"] == "ALREADY_FILLED", str(rejected))

    fake.published.clear()
    await handlers.handle_cancel_requested({"event_id": "e4", "payload": {
        "order_id": "99999999-9999-9999-9999-999999999999", "user_id": "x",
        "symbol": "AAPL", "side": "BUY", "requested_at": "2026-08-10T00:00:00.000000Z",
    }})
    check("cancel of an unknown order -> NOT_IN_BOOK",
          fake.published[0][1]["reason"] == "NOT_IN_BOOK", str(fake.published))

    # cancel a genuinely resting order, partially filled first
    fake.published.clear()
    resting = {**accepted, "order_id": "33333333-3333-3333-3333-333333333333", "quantity": 10}
    await handlers.handle_order_accepted({"event_id": "e5a", "payload": resting})
    await handlers.handle_order_accepted({"event_id": "e5b", "payload": {
        **taker, "order_id": "44444444-4444-4444-4444-444444444444", "quantity": 4,
    }})
    fake.published.clear()
    await handlers.handle_cancel_requested({"event_id": "e5", "payload": {
        "order_id": resting["order_id"], "user_id": resting["user_id"],
        "symbol": "AAPL", "side": "SELL", "requested_at": "2026-08-10T00:00:00.000000Z",
    }})
    key, event = fake.published[0]
    check("cancel of a resting order -> order.cancelled", key == "order.cancelled", key)
    check("cancel reports only the UNFILLED remainder (6, not 10)",
          event["cancelled_quantity"] == 6 and event["reason"] == "USER_REQUEST", str(event))

    # an unknown symbol must never create a book
    fake.published.clear()
    await handlers.handle_order_accepted({"event_id": "e6", "payload": {**accepted, "symbol": "DOGE"}})
    check("unknown symbol is dropped, not booked", "DOGE" not in books and fake.published == [],
          str(list(books)[:3]))

    handlers.broker = None


asyncio.run(contract_checks())

print("=" * 60)
if FAILURES:
    print(f"\n{len(FAILURES)} FAILURE(S):")
    for failure in FAILURES:
        print(f"  - {failure}")
    sys.exit(1)
print("\nall matching engine checks passed\n")
