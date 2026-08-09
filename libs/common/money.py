"""Money and quantity rules. EVERY service must use these — no floats, ever.

Rules (contract):
  * All monetary values are Decimal, quantized to 4 decimal places, ROUND_HALF_UP.
  * All monetary values cross the wire (JSON) as STRINGS: "100.5000".
  * Quantity is a positive Python int (whole shares). Never a Decimal, never a float.
  * Limit prices must be a positive multiple of the 0.01 tick.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MONEY_QUANT = Decimal("0.0001")
TICK = Decimal("0.01")
MAX_PRICE = Decimal("1000000.0000")
MAX_QTY = 1_000_000


class MoneyError(ValueError):
    """Raised when a value cannot be represented as valid money."""


def to_money(value) -> Decimal:
    """Parse anything (str/int/Decimal) into a 4dp Decimal. Rejects float input."""
    if isinstance(value, float):
        raise MoneyError("float is not accepted for money; pass str or Decimal")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MoneyError(f"invalid money value: {value!r}") from exc
    if not d.is_finite():
        raise MoneyError("non-finite money value")
    return d.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def money_str(value) -> str:
    """Canonical wire form: fixed-point, no exponent. to_money first."""
    return format(to_money(value), "f")


def validate_price(value) -> Decimal:
    price = to_money(value)
    if price <= 0:
        raise MoneyError("price must be greater than 0")
    if price > MAX_PRICE:
        raise MoneyError(f"price exceeds maximum {MAX_PRICE}")
    if (price % TICK) != 0:
        raise MoneyError("price must be a multiple of 0.01")
    return price


def validate_quantity(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyError("quantity must be an integer number of shares")
    if value <= 0:
        raise MoneyError("quantity must be greater than 0")
    if value > MAX_QTY:
        raise MoneyError(f"quantity exceeds maximum {MAX_QTY}")
    return value


def notional(price, quantity: int) -> Decimal:
    """price * quantity, quantized. Use for every cash amount computation."""
    return to_money(to_money(price) * Decimal(validate_quantity(quantity)))
