#!/usr/bin/env python3
"""Order Service self-check — the two pure pieces, no database, no broker.

  1. `validate()`   — every 400 the order ticket can provoke.
  2. `transition()` — the state machine, including "a terminal order never
                      changes again", which is the single rule that keeps a
                      late fill from resurrecting a cancelled order.

    python services/order-service/selfcheck.py
"""

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "libs"), str(Path(__file__).resolve().parent)]
# deps.py builds the engine at import time; give it something parseable.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://mse:mse_pw@localhost:5435/order_db")

from app.models import (  # noqa: E402
    CANCEL_PENDING, CANCELLED, FILLED, NEW, PARTIALLY_FILLED, PENDING, REJECTED, Order,
)
from app.schemas import PlaceOrderIn  # noqa: E402
from app.service import transition, validate  # noqa: E402
from fastapi import HTTPException  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  {detail}")


def body(**kw) -> PlaceOrderIn:
    base = {"symbol": "AAPL", "side": "BUY", "order_type": "LIMIT", "price": "100.00", "quantity": 10}
    base.update(kw)
    return PlaceOrderIn(**base)


def rejects(name: str, **kw) -> None:
    try:
        validate(body(**kw))
    except HTTPException as exc:
        check(f"400 {name}", exc.status_code == 400, f"got {exc.status_code}: {exc.detail}")
    else:
        check(f"400 {name}", False, "accepted a value it should have refused")


class FakeSession:
    """transition() only ever calls session.add()."""

    def __init__(self) -> None:
        self.rows: list = []

    def add(self, row) -> None:
        self.rows.append(row)


def order_at(status: str, **kw) -> Order:
    import uuid
    o = Order(
        id=uuid.uuid4(), user_id=uuid.uuid4(), symbol="AAPL", side="BUY",
        order_type="LIMIT", price=Decimal("100"), quantity=10, status=status, **kw
    )
    o.filled_quantity = kw.get("filled_quantity", 0)
    return o


async def main() -> None:
    print("\norder service selfcheck")
    print("=" * 60)

    # ---------------------------------------------------------------- validate
    symbol, side, order_type, price, quantity = validate(body())
    check("valid LIMIT BUY parses",
          (symbol, side, order_type, price, quantity) == ("AAPL", "BUY", "LIMIT", Decimal("100.0000"), 10),
          f"{symbol} {side} {order_type} {price} {quantity}")

    _, _, _, price, _ = validate(body(symbol="aapl", side="sell", order_type="market", price=None))
    check("lowercase symbol/side/type normalise, MARKET has no price", price is None, str(price))

    rejects("unknown symbol", symbol="DOGE")
    rejects("empty symbol", symbol="")
    rejects("bad side", side="LONG")
    rejects("bad order type", order_type="STOP")

    rejects("sub-tick price", price="100.005")
    rejects("scientific notation price", price="1e5")
    rejects("NaN price", price="NaN")
    rejects("Infinity price", price="Infinity")
    rejects("negative price", price="-5.00")
    rejects("zero price", price="0")
    rejects("missing price on LIMIT", price=None)
    rejects("price supplied on MARKET", order_type="MARKET", price="100.00")

    rejects("zero quantity", quantity=0)
    rejects("negative quantity", quantity=-5)
    rejects("fractional quantity", quantity=10.5)
    rejects("string quantity", quantity="10")
    rejects("boolean quantity", quantity=True)
    rejects("missing quantity", quantity=None)
    rejects("absurd quantity", quantity=10_000_000)

    # -------------------------------------------------------------- transition
    session = FakeSession()

    o = order_at(PENDING)
    check("PENDING -> NEW allowed", await transition(session, o, NEW) and o.status == NEW, o.status)
    check("transition writes an audit row", len(session.rows) == 1, str(len(session.rows)))

    o = order_at(PENDING)
    check("PENDING -> REJECTED allowed", await transition(session, o, REJECTED), o.status)

    o = order_at(PENDING)
    check("PENDING -> FILLED refused", not await transition(session, o, FILLED) and o.status == PENDING, o.status)

    o = order_at(NEW)
    check("NEW -> PARTIALLY_FILLED allowed", await transition(session, o, PARTIALLY_FILLED), o.status)
    check("PARTIALLY_FILLED -> PARTIALLY_FILLED allowed (repeat fills)",
          await transition(session, o, PARTIALLY_FILLED), o.status)
    check("PARTIALLY_FILLED -> FILLED allowed", await transition(session, o, FILLED), o.status)

    for terminal in (FILLED, CANCELLED, REJECTED):
        for target in (NEW, PARTIALLY_FILLED, FILLED, CANCELLED):
            o = order_at(terminal)
            moved = await transition(session, o, target)
            check(f"terminal {terminal} -> {target} refused", not moved and o.status == terminal, o.status)

    o = order_at(NEW)
    check("NEW -> CANCEL_PENDING allowed", await transition(session, o, CANCEL_PENDING), o.status)
    check("CANCEL_PENDING -> CANCELLED allowed", await transition(session, o, CANCELLED), o.status)

    # cancel_rejected reverts CANCEL_PENDING to whatever it really was
    for filled, expected in ((0, NEW), (4, PARTIALLY_FILLED), (10, FILLED)):
        o = order_at(CANCEL_PENDING, filled_quantity=filled)
        check(f"CANCEL_PENDING reverts to {expected} at filled={filled}",
              await transition(session, o, expected) and o.status == expected, o.status)

    print("=" * 60)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        sys.exit(1)
    print("\nall order service checks passed\n")


asyncio.run(main())
