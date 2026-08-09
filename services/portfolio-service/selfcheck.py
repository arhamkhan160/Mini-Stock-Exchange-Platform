import uuid
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from common.money import to_money
# A full self check would test the database and redis interactions.
# We will just assert some basics here as a placeholder for local checks.

def test_pnl_math():
    qty = 0
    avg_cost = Decimal("0.0000")
    
    # BUY 10 @ 100
    fill_qty = 10
    fill_price = Decimal("100.0000")
    new_qty = qty + fill_qty
    new_avg = to_money((qty * avg_cost + fill_qty * fill_price) / new_qty)
    qty, avg_cost = new_qty, new_avg
    assert qty == 10
    assert avg_cost == Decimal("100.0000")
    
    # BUY 10 @ 200
    fill_qty = 10
    fill_price = Decimal("200.0000")
    new_qty = qty + fill_qty
    new_avg = to_money((qty * avg_cost + fill_qty * fill_price) / new_qty)
    qty, avg_cost = new_qty, new_avg
    assert qty == 20
    assert avg_cost == Decimal("150.0000")
    
    # SELL 10 @ 180
    fill_qty = 10
    fill_price = Decimal("180.0000")
    realized = to_money((fill_price - avg_cost) * fill_qty)
    qty -= fill_qty
    assert qty == 10
    assert realized == Decimal("300.0000")
    assert avg_cost == Decimal("150.0000")

def run():
    test_pnl_math()
    print("All selfcheck tests passed!")

if __name__ == "__main__":
    run()
