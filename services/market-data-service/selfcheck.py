import uuid
from datetime import datetime, timezone
from common.money import to_money
from app.events import floor_bucket

def test_floor_bucket():
    ts = datetime(2026, 8, 9, 12, 3, 47, tzinfo=timezone.utc)
    assert floor_bucket(ts, 1) == datetime(2026, 8, 9, 12, 3, 0, tzinfo=timezone.utc)
    assert floor_bucket(ts, 5) == datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)
    
    ts2 = datetime(2026, 8, 9, 12, 5, 0, tzinfo=timezone.utc)
    assert floor_bucket(ts2, 5) == datetime(2026, 8, 9, 12, 5, 0, tzinfo=timezone.utc)
    
    ts3 = datetime(2026, 8, 9, 23, 59, 59, tzinfo=timezone.utc)
    assert floor_bucket(ts3, 5) == datetime(2026, 8, 9, 23, 55, 0, tzinfo=timezone.utc)

def run():
    test_floor_bucket()
    print("All selfcheck tests passed!")

if __name__ == "__main__":
    run()
