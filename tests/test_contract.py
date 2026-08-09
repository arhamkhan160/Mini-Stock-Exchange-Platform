"""Contract tests for `libs/common` — the shared kernel every service depends on.

Plain asserts, no pytest. If any of these fail, all seven services are wrong in
the same way, so this is the first thing to run after touching libs/common.

    python tests/test_contract.py

Checks that need a dependency which is not installed are SKIPPED with a reason
rather than failing, so the file runs both on a bare machine and inside a
service container (where everything is available).
"""

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs"))


class Skip(Exception):
    """Raised by a check whose dependency is unavailable."""


CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def raises(exc_types, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except exc_types:
        return
    except Exception as unexpected:
        raise AssertionError(f"{fn.__name__}{args} raised {unexpected!r}, expected {exc_types}")
    raise AssertionError(f"{fn.__name__}{args} should have raised {exc_types}")


# --------------------------------------------------------------------------- #
# money — the single biggest source of wrong numbers if it drifts
# --------------------------------------------------------------------------- #
@check
def money_parses_and_quantizes():
    from common.money import MoneyError, to_money

    assert to_money("195.5") == Decimal("195.5000")
    assert to_money(195) == Decimal("195.0000")
    assert to_money(Decimal("0.1")) == Decimal("0.1000")
    # ROUND_HALF_UP, not banker's rounding
    assert to_money("0.00005") == Decimal("0.0001")
    assert to_money("0.00015") == Decimal("0.0002")
    raises(MoneyError, to_money, "abc")
    raises(MoneyError, to_money, None)
    raises(MoneyError, to_money, "NaN")
    raises(MoneyError, to_money, "Infinity")


@check
def money_rejects_float():
    """A float here is how 10000.000000000002 reaches a user's balance."""
    from common.money import MoneyError, to_money

    raises(MoneyError, to_money, 195.5)
    raises(MoneyError, to_money, 0.1)


@check
def money_wire_format_is_fixed_point_string():
    from common.money import money_str

    assert money_str(Decimal("195.5")) == "195.5000"
    assert money_str("0") == "0.0000"
    assert money_str(1000000) == "1000000.0000"
    # never exponent notation, which json consumers cannot parse as money
    assert "E" not in money_str(Decimal("1e-4"))
    assert money_str(Decimal("1e-4")) == "0.0001"


@check
def price_must_be_positive_and_tick_aligned():
    from common.money import MoneyError, validate_price

    assert validate_price("100.01") == Decimal("100.0100")
    assert validate_price("195.50") == Decimal("195.5000")
    raises(MoneyError, validate_price, "100.005")   # sub-tick
    raises(MoneyError, validate_price, "0")
    raises(MoneyError, validate_price, "-1")
    raises(MoneyError, validate_price, "9999999")   # over MAX_PRICE


@check
def quantity_must_be_a_positive_whole_number():
    from common.money import MoneyError, validate_quantity

    assert validate_quantity(10) == 10
    raises(MoneyError, validate_quantity, 0)
    raises(MoneyError, validate_quantity, -5)
    raises(MoneyError, validate_quantity, 10.5)
    raises(MoneyError, validate_quantity, "10")
    # bool is an int subclass in Python — True must not become quantity 1
    raises(MoneyError, validate_quantity, True)
    raises(MoneyError, validate_quantity, 2_000_000)


@check
def notional_is_exact():
    from common.money import notional

    assert notional("195.50", 10) == Decimal("1955.0000")
    assert notional("0.0001", 3) == Decimal("0.0003")
    assert notional(Decimal("33.3333"), 3) == Decimal("99.9999")


# --------------------------------------------------------------------------- #
# symbols
# --------------------------------------------------------------------------- #
@check
def symbol_universe_is_consistent():
    from common.symbols import SEED_PRICES, SYMBOLS

    assert len(SYMBOLS) == 8, f"expected 8 tradable symbols, got {len(SYMBOLS)}"
    # Drift here means the seeder or a MARKET order reservation blows up on a
    # symbol that has no reference price.
    assert set(SEED_PRICES) == set(SYMBOLS), (
        f"SEED_PRICES and SYMBOLS disagree: {set(SEED_PRICES) ^ set(SYMBOLS)}"
    )


@check
def every_seed_price_is_a_valid_limit_price():
    from common.money import validate_price
    from common.symbols import SEED_PRICES

    for symbol, price in SEED_PRICES.items():
        validate_price(price)  # raises if not positive and tick-aligned


@check
def symbol_normalisation():
    from common.symbols import is_valid_symbol, normalize_symbol

    assert normalize_symbol("aapl") == "AAPL"
    assert normalize_symbol("  tsla  ") == "TSLA"
    assert is_valid_symbol("nvda") is True
    assert is_valid_symbol("FAKE") is False
    raises(ValueError, normalize_symbol, "FAKE")
    raises(ValueError, normalize_symbol, None)


# --------------------------------------------------------------------------- #
# events — a queue-name collision silently steals another service's messages
# --------------------------------------------------------------------------- #
@check
def envelope_shape():
    from common.events import TRADE_EXECUTED, envelope

    env = envelope(TRADE_EXECUTED, {"trade_id": "x"})
    assert set(env) == {"event_id", "event_type", "occurred_at", "version", "payload"}
    assert env["event_type"] == "trade.executed"
    assert env["version"] == 1
    assert env["payload"] == {"trade_id": "x"}
    assert env["occurred_at"].endswith("Z")
    # must parse as UTC on the consumer side
    datetime.fromisoformat(env["occurred_at"].replace("Z", "+00:00"))


@check
def every_event_id_is_unique():
    from common.events import ORDER_ACCEPTED, envelope

    ids = {envelope(ORDER_ACCEPTED, {})["event_id"] for _ in range(500)}
    assert len(ids) == 500, "event_id collisions would break idempotency guards"


@check
def routing_keys_are_complete_and_distinct():
    from common import events

    expected = {
        "order.accepted",
        "order.rejected",
        "order.cancel_requested",
        "order.cancelled",
        "order.cancel_rejected",
        "trade.executed",
    }
    assert set(events.ALL_ROUTING_KEYS) == expected, set(events.ALL_ROUTING_KEYS) ^ expected
    assert len(events.ALL_ROUTING_KEYS) == len(set(events.ALL_ROUTING_KEYS))


@check
def queue_names_are_unique():
    """Two services sharing a queue means each gets only half the events."""
    from common import events

    names = [
        value
        for name, value in vars(events).items()
        if name.startswith("Q_") and isinstance(value, str)
    ]
    assert len(names) == 15, f"expected 15 declared queues, found {len(names)}"
    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, f"duplicate queue names: {duplicates}"
    assert all(n.startswith("q.") for n in names), "queue names must be namespaced with 'q.'"


@check
def exchanges_are_distinct():
    from common.events import DEAD_EXCHANGE, EXCHANGE

    assert EXCHANGE != DEAD_EXCHANGE
    assert EXCHANGE == "exchange.events"


# --------------------------------------------------------------------------- #
# redis key catalog — Portfolio and Order read keys Market Data writes
# --------------------------------------------------------------------------- #
@check
def redis_helpers_use_the_agreed_key_shapes():
    import inspect

    from common import redis_client

    source = inspect.getsource(redis_client)
    for key in ("md:last_price:", "idem:", "md:ticks"):
        assert key in source, f"redis key catalog lost '{key}'"
    # The claim-before-work bug: a SET NX in the idempotency check would mean a
    # crash mid-handler silently drops the event forever.
    assert "def seen_event" in source and "def mark_event_processed" in source
    seen = inspect.getsource(redis_client.seen_event)
    assert "nx=True" not in seen, "seen_event must be READ ONLY, never claim the event"


# --------------------------------------------------------------------------- #
# security — needs fastapi, skipped where it cannot be imported
# --------------------------------------------------------------------------- #
def _security():
    try:
        from common import security
    except ModuleNotFoundError as exc:
        raise Skip(f"needs {exc.name} (run inside a service container)")
    return security


@check
def jwt_round_trip():
    import uuid

    security = _security()
    user_id = uuid.uuid4()
    token = security.create_access_token(user_id, "a@b.com", "arham")
    claims = security.decode_token(token)

    # PyJWT >= 2.10 rejects a non-string `sub` on decode.
    assert claims["sub"] == str(user_id) and isinstance(claims["sub"], str)
    assert claims["email"] == "a@b.com"
    assert claims["username"] == "arham"
    assert claims["type"] == "access"
    assert claims["exp"] > claims["iat"]


@check
def jwt_rejects_tampering_and_expiry():
    import time
    import uuid

    import jwt as pyjwt

    security = _security()
    from common.config import settings

    good = security.create_access_token(uuid.uuid4(), "a@b.com", "arham")
    tampered = good[:-3] + ("aaa" if not good.endswith("aaa") else "bbb")
    raises(Exception, security.decode_token, tampered)

    import warnings

    with warnings.catch_warnings():
        # The attacker's key is deliberately not ours; PyJWT's key-length
        # warning is noise in this test.
        warnings.simplefilter("ignore")
        signed_elsewhere = pyjwt.encode(
            {"sub": "x", "exp": time.time() + 60}, "an-attacker-key-that-is-not-ours"
        )
    raises(Exception, security.decode_token, signed_elsewhere)

    expired = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "iat": int(time.time()) - 100,
         "exp": int(time.time()) - 10, "type": "access"},
        settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM,
    )
    raises(Exception, security.decode_token, expired)

    # A refresh-shaped token must not be accepted as an access token.
    wrong_type = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "exp": int(time.time()) + 60, "type": "refresh"},
        settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM,
    )
    raises(Exception, security.decode_token, wrong_type)


def main() -> int:
    passed = failed = skipped = 0
    for fn in CHECKS:
        label = fn.__name__.replace("_", " ")
        try:
            fn()
        except Skip as reason:
            # ASCII only: the Windows console default codepage mangles dashes.
            print(f"  SKIP  {label} - {reason}")
            skipped += 1
        except AssertionError as exc:
            print(f"  FAIL  {label}\n        {exc}")
            failed += 1
        except Exception as exc:  # noqa: BLE001 - report, do not stop the run
            print(f"  ERROR {label}\n        {exc!r}")
            failed += 1
        else:
            print(f"  OK    {label}")
            passed += 1

    print(f"\ncontract: {passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
