"""Populate the running stack with a realistic amount of demo data.

Everything that CAN go through the public API does, so this doubles as a
broad end-to-end exercise: registration, deposits, order placement, matching,
settlement, portfolio projection and notifications all run for real.

The one exception is the initial share allocation. There is deliberately no
"give me shares" endpoint — you can only obtain shares by buying them. That
creates a bootstrap deadlock on a brand-new exchange: nobody can sell because
nobody owns anything, so no trade ever prints. Real venues solve this with an
issuance/IPO step, and that is what the direct `holdings` insert below models.

Usage:
    python infra/seed/seed_demo_data.py                # default volume
    python infra/seed/seed_demo_data.py --users 20 --rounds 6

Requires the stack to be up (`docker compose up -d`).
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs"))

from common.symbols import SEED_PRICES, SYMBOLS  # noqa: E402

GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8000")

# Seeded accounts use @mse.local, which the gateway rate limiter treats as bot
# traffic (1200/min instead of 30/min). Without this the seeder throttles itself.
DOMAIN = "mse.local"
PASSWORD = "SeedPassw0rd!"
# The market-maker bot owns this identity; keep one canonical demo login.
DEMO_PASSWORD = "DemoPassw0rd!"

STARTING_CASH = "500000.00"
DEMO_CASH = "100000.00"


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


class Trader:
    def __init__(self, client: httpx.Client, email: str, username: str,
                 password: str = PASSWORD) -> None:
        self.client = client
        self.email = email
        self.username = username
        self.password = password
        self.token: str | None = None
        self.user_id: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def register_or_login(self) -> None:
        response = self.client.post(
            "/api/auth/register",
            json={"email": self.email, "username": self.username, "password": self.password},
        )
        if response.status_code == 201:
            body = response.json()
        else:  # already exists — log in instead, so re-running is safe
            response = self.client.post(
                "/api/auth/login",
                data={"username": self.email, "password": self.password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            body = response.json()
        self.token = body["access_token"]
        self.user_id = body["user"]["id"]

    def deposit(self, amount: str) -> None:
        self.client.post("/api/account/deposit", json={"amount": amount}, headers=self.headers)

    def place(self, symbol: str, side: str, order_type: str, quantity: int, price: str | None = None):
        payload = {
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "client_order_id": str(uuid.uuid4()),
        }
        if price is not None:
            payload["price"] = price
        return self.client.post("/api/orders", json=payload, headers=self.headers)


def tick(value: Decimal) -> str:
    """Snap to the 0.01 tick the contract requires."""
    return f"{value.quantize(Decimal('0.01')):f}"


def allocate_shares(traders: list[Trader], per_symbol: int, symbols: list[str]) -> int:
    """The issuance step — see the module docstring for why this is direct SQL."""
    import asyncio

    import asyncpg

    user = env_value("POSTGRES_USER", "mse")
    password = env_value("POSTGRES_PASSWORD", "mse_pw")
    dsn = f"postgresql://{user}:{password}@localhost:5438/portfolio_db"

    async def run() -> int:
        conn = await asyncpg.connect(dsn)
        written = 0
        try:
            for trader in traders:
                for symbol in symbols:
                    quantity = random.randint(per_symbol // 2, per_symbol)
                    avg_cost = Decimal(SEED_PRICES[symbol]) * Decimal("0.98")
                    await conn.execute(
                        """
                        INSERT INTO holdings (id, user_id, symbol, quantity, reserved_quantity,
                                              avg_cost, realized_pnl, updated_at)
                        VALUES ($1, $2, $3, $4, 0, $5, 0, now())
                        ON CONFLICT (user_id, symbol) DO UPDATE
                          SET quantity = holdings.quantity + EXCLUDED.quantity
                        """,
                        uuid.uuid4(), uuid.UUID(trader.user_id), symbol,
                        quantity, avg_cost.quantize(Decimal("0.0001")),
                    )
                    written += 1
        finally:
            await conn.close()
        return written

    return asyncio.run(run())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=14)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--shares", type=int, default=600, help="max shares issued per symbol per user")
    args = parser.parse_args()

    random.seed(7)  # reproducible volume
    symbols = list(SYMBOLS)

    with httpx.Client(base_url=GATEWAY, timeout=30.0) as client:
        # ---- accounts -----------------------------------------------------
        print(f"Creating {args.users} traders...")
        traders: list[Trader] = []
        for i in range(args.users):
            trader = Trader(client, f"trader{i:02d}@{DOMAIN}", f"trader{i:02d}")
            trader.register_or_login()
            trader.deposit(STARTING_CASH)
            traders.append(trader)
        print(f"  {len(traders)} traders funded with ${STARTING_CASH} each")

        demo = Trader(client, f"demo@{DOMAIN}", "demo", DEMO_PASSWORD)
        demo.register_or_login()
        demo.deposit(DEMO_CASH)
        print(f"  demo account ready: demo@{DOMAIN} / {DEMO_PASSWORD}")

        # ---- issuance -----------------------------------------------------
        rows = allocate_shares(traders, args.shares, symbols)
        print(f"Issued shares: {rows} holding rows across {len(symbols)} symbols")

        # ---- resting liquidity --------------------------------------------
        print(f"Placing resting orders over {args.rounds} rounds...")
        placed = filled = rejected = 0
        for round_index in range(args.rounds):
            for symbol in symbols:
                reference = Decimal(SEED_PRICES[symbol])
                makers = random.sample(traders, k=min(6, len(traders)))
                for depth, trader in enumerate(makers, start=1):
                    step = Decimal("0.001") * depth
                    bid = reference * (Decimal(1) - step)
                    ask = reference * (Decimal(1) + step)
                    quantity = random.choice([10, 20, 25, 40, 50])

                    r1 = trader.place(symbol, "BUY", "LIMIT", quantity, tick(bid))
                    r2 = trader.place(symbol, "SELL", "LIMIT", quantity, tick(ask))
                    for r in (r1, r2):
                        placed += 1
                        if r.status_code >= 400:
                            rejected += 1

                # ---- crossing trades: this is what actually prints tape ----
                for _ in range(3):
                    buyer, seller = random.sample(traders, 2)
                    quantity = random.choice([5, 10, 15, 20])
                    cross = reference * (Decimal(1) + Decimal("0.002"))
                    if seller.place(symbol, "SELL", "LIMIT", quantity, tick(reference)).status_code < 400:
                        placed += 1
                    if buyer.place(symbol, "BUY", "LIMIT", quantity, tick(cross)).status_code < 400:
                        placed += 1
                        filled += 1
            print(f"  round {round_index + 1}/{args.rounds} done ({placed} orders so far)")
            time.sleep(1)  # let the engine and the projections catch up

        # ---- market orders and cancellations, to exercise those paths ------
        print("Exercising market orders and cancellations...")
        for symbol in symbols[:4]:
            taker = random.choice(traders)
            taker.place(symbol, "BUY", "MARKET", 5)
            taker.place(symbol, "SELL", "MARKET", 5)

        cancelled = 0
        for trader in traders[:6]:
            open_orders = client.get(
                "/api/orders", params={"status": "NEW", "limit": 5}, headers=trader.headers
            )
            if open_orders.status_code == 200:
                for order in open_orders.json()[:2]:
                    if client.delete(f"/api/orders/{order['id']}", headers=trader.headers).status_code < 400:
                        cancelled += 1
        print(f"  cancelled {cancelled} resting orders")

        print("\nWaiting for projections to settle...")
        time.sleep(6)

        # ---- summary -------------------------------------------------------
        book = client.get("/api/book/AAPL").json()
        portfolio = client.get("/api/portfolio", headers=traders[0].headers).json()
        notifications = client.get(
            "/api/notifications", params={"limit": 5}, headers=traders[0].headers
        ).json()

        print("\n" + "=" * 60)
        print(f"orders placed        : {placed}")
        print(f"rejected (expected)  : {rejected}")
        print(f"crossing buys        : {filled}")
        print(f"AAPL book            : {len(book['bids'])} bid levels / {len(book['asks'])} ask levels")
        print(f"trader00 holdings    : {len(portfolio.get('holdings', []))} symbols")
        print(f"trader00 notifications: {len(notifications) if isinstance(notifications, list) else 0}")
        print(f"\ndemo login   : demo@{DOMAIN} / {DEMO_PASSWORD}")
        print(f"trader logins: trader00..trader{args.users - 1:02d}@{DOMAIN} / {PASSWORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
