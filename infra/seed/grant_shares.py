"""Grant shares to a user — the issuance / IPO step.

There is deliberately no API endpoint for this. In the running system the ONLY
way to obtain shares is to buy them, which is what makes the share-reservation
check meaningful (it is what stops a user selling stock they do not own). A
real venue creates shares through issuance, and that is what this models.

    # give the demo account a starting portfolio across every symbol
    python infra/seed/grant_shares.py --user demo@mse.local --all --qty 300

    # top up one position
    python infra/seed/grant_shares.py --user demo@mse.local --symbol AAPL --qty 500

    # show what somebody holds
    python infra/seed/grant_shares.py --user demo@mse.local --show

Quantities ADD to any existing position, and the average cost is recomputed as
a proper weighted average so the portfolio's P&L stays honest.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs"))

from common.symbols import SEED_PRICES, SYMBOLS  # noqa: E402


def env_value(key: str, default: str) -> str:
    if key in os.environ:
        return os.environ[key]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return default


PG_USER = env_value("POSTGRES_USER", "mse")
PG_PASS = env_value("POSTGRES_PASSWORD", "mse_pw")
USER_DSN = f"postgresql://{PG_USER}:{PG_PASS}@localhost:5433/user_db"
PORTFOLIO_DSN = f"postgresql://{PG_USER}:{PG_PASS}@localhost:5438/portfolio_db"


async def resolve_user(conn, identifier: str) -> tuple[uuid.UUID, str] | None:
    row = await conn.fetchrow(
        "SELECT id, email FROM users WHERE lower(email) = lower($1) OR lower(username) = lower($1)",
        identifier,
    )
    return (row["id"], row["email"]) if row else None


async def run(args) -> int:
    import asyncpg

    user_conn = await asyncpg.connect(USER_DSN)
    try:
        found = await resolve_user(user_conn, args.user)
    finally:
        await user_conn.close()

    if not found:
        print(f"No user matches {args.user!r}. Register them first, or run seed_demo_data.py.")
        return 1
    user_id, email = found

    conn = await asyncpg.connect(PORTFOLIO_DSN)
    try:
        if args.show:
            rows = await conn.fetch(
                "SELECT symbol, quantity, reserved_quantity, avg_cost, realized_pnl "
                "FROM holdings WHERE user_id = $1 ORDER BY symbol",
                user_id,
            )
            print(f"\nHoldings for {email}:")
            if not rows:
                print("  (none)")
            for r in rows:
                available = r["quantity"] - r["reserved_quantity"]
                print(f"  {r['symbol']:<6} qty={r['quantity']:<7} available={available:<7} "
                      f"avg_cost={r['avg_cost']:<12} realized={r['realized_pnl']}")
            return 0

        symbols = list(SYMBOLS) if args.all else [args.symbol.strip().upper()]
        unknown = [s for s in symbols if s not in SYMBOLS]
        if unknown:
            print(f"Unknown symbol(s): {unknown}. Known: {', '.join(SYMBOLS)}")
            return 1

        for symbol in symbols:
            price = Decimal(args.price) if args.price else Decimal(SEED_PRICES[symbol])
            # Weighted average across the existing position and this grant, so
            # unrealized P&L stays meaningful instead of being reset.
            await conn.execute(
                """
                INSERT INTO holdings (id, user_id, symbol, quantity, reserved_quantity,
                                      avg_cost, realized_pnl, updated_at)
                VALUES ($1, $2, $3, $4, 0, $5, 0, now())
                ON CONFLICT (user_id, symbol) DO UPDATE SET
                    avg_cost = round(
                        (holdings.quantity * holdings.avg_cost + EXCLUDED.quantity * EXCLUDED.avg_cost)
                        / NULLIF(holdings.quantity + EXCLUDED.quantity, 0), 4),
                    quantity = holdings.quantity + EXCLUDED.quantity,
                    updated_at = now()
                """,
                uuid.uuid4(), user_id, symbol, args.qty, price.quantize(Decimal("0.0001")),
            )
            print(f"  granted {args.qty:>6} {symbol:<6} @ {price}")

        print(f"\nDone. {email} now holds:")
        rows = await conn.fetch(
            "SELECT symbol, quantity, avg_cost FROM holdings WHERE user_id = $1 ORDER BY symbol",
            user_id,
        )
        for r in rows:
            print(f"  {r['symbol']:<6} {r['quantity']:>7} @ avg {r['avg_cost']}")
        print("\nRefresh the portfolio page to see it.")
        return 0
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant shares to a user (issuance).")
    parser.add_argument("--user", required=True, help="email or username")
    parser.add_argument("--symbol", help="one symbol, e.g. AAPL")
    parser.add_argument("--all", action="store_true", help="every symbol")
    parser.add_argument("--qty", type=int, default=300, help="shares to add (default 300)")
    parser.add_argument("--price", help="cost basis to record (default: the symbol's seed price)")
    parser.add_argument("--show", action="store_true", help="just print current holdings")
    args = parser.parse_args()

    if not args.show and not args.symbol and not args.all:
        parser.error("pass --symbol SYMBOL, or --all, or --show")
    if args.qty <= 0 and not args.show:
        parser.error("--qty must be positive")

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
