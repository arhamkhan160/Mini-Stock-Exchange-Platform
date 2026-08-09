"""The fixed universe of tradable symbols.

Kept as a static constant so that Order, Matching Engine, Portfolio and the
Frontend can validate a symbol WITHOUT a network call to Market Data.
Market Data still owns the `symbols` table (name, seed price) in its database.
"""

SYMBOLS: dict[str, str] = {
    "AAPL": "Apple Inc.",
    "GOOGL": "Alphabet Inc.",
    "MSFT": "Microsoft Corporation",
    "AMZN": "Amazon.com Inc.",
    "TSLA": "Tesla Inc.",
    "NVDA": "NVIDIA Corporation",
    "META": "Meta Platforms Inc.",
    "NFLX": "Netflix Inc.",
}

# Seed / reference opening prices used by the seeder and as a last-resort
# reference for MARKET orders when Redis has no last price yet.
SEED_PRICES: dict[str, str] = {
    "AAPL": "195.50",
    "GOOGL": "175.20",
    "MSFT": "425.80",
    "AMZN": "185.40",
    "TSLA": "245.60",
    "NVDA": "128.30",
    "META": "512.10",
    "NFLX": "685.90",
}


def is_valid_symbol(symbol: str) -> bool:
    return isinstance(symbol, str) and symbol.upper() in SYMBOLS


def normalize_symbol(symbol: str) -> str:
    """Uppercase and validate. Raises ValueError for unknown symbols."""
    if not isinstance(symbol, str):
        raise ValueError("symbol must be a string")
    s = symbol.strip().upper()
    if s not in SYMBOLS:
        raise ValueError(f"unknown symbol: {symbol!r}")
    return s
