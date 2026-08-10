"""Live market simulator — run this in the background during the presentation.

Keeps the exchange visibly alive: prices drift on a random walk, trades print
every couple of seconds, candles form minute by minute, the order book stays
two-sided, and WebSocket ticks keep the chart moving while you talk.

Everything goes through the public API, exactly as a browser would, so it
exercises the real path: gateway -> order -> account/portfolio -> matching
engine -> events -> settlement -> notifications.

    python infra/seed/live_market.py                 # run until Ctrl+C
    python infra/seed/live_market.py --minutes 45    # stop on its own
    python infra/seed/live_market.py --symbols AAPL,TSLA --speed 1.0

Requires the stack up and `seed_demo_data.py` already run (it uses those
trader accounts and their share inventory).
"""

from __future__ import annotations

import argparse
import os
import random
import signal
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs"))

from common.symbols import SEED_PRICES, SYMBOLS  # noqa: E402

GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8000")

# Seeded identities. The @mse.local domain gets the gateway's bot rate limit
# (1200/min instead of 30/min), so the simulator never throttles itself.
DOMAIN = "mse.local"
PASSWORD = "SeedPassw0rd!"
TRADER_COUNT = 14

# Random-walk parameters. Small enough to look like a real tape, large enough
# that the candles visibly move during a 20-minute presentation.
DRIFT = 0.00035          # per step, as a fraction of price
MAX_EXCURSION = 0.06     # never wander more than 6% from the opening price
TOP_UP_EVERY = 60        # deposit more cash every N steps, so buyers never stall

_running = True


def _stop(signum, frame):
    global _running
    _running = False
    print("\nstopping after this step...")


class Trader:
    def __init__(self, client: httpx.Client, index: int) -> None:
        self.client = client
        self.email = f"trader{index:02d}@{DOMAIN}"
        self.token: str | None = None
        self.name = f"trader{index:02d}"

    def login(self) -> bool:
        r = self.client.post(
            "/api/auth/login-json",
            json={"email_or_username": self.email, "password": PASSWORD},
        )
        if r.status_code != 200:
            return False
        self.token = r.json()["access_token"]
        return True

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def place(self, symbol: str, side: str, quantity: int, price: str) -> int:
        try:
            r = self.client.post(
                "/api/orders",
                headers=self.headers,
                json={
                    "symbol": symbol,
                    "side": side,
                    "order_type": "LIMIT",
                    "price": price,
                    "quantity": quantity,
                },
            )
            return r.status_code
        except httpx.HTTPError:
            return 0

    def deposit(self, amount: str = "250000.00") -> None:
        try:
            self.client.post("/api/account/deposit", headers=self.headers, json={"amount": amount})
        except httpx.HTTPError:
            pass


def tick(value: Decimal) -> str:
    """Snap to the 0.01 tick size the contract requires."""
    return f"{value.quantize(Decimal('0.01')):f}"


def current_prices(client: httpx.Client, symbols: list[str]) -> dict[str, Decimal]:
    """Start from the live market so the walk continues from where it is."""
    prices: dict[str, Decimal] = {}
    try:
        for row in client.get("/api/market/symbols").json():
            if row["symbol"] in symbols:
                prices[row["symbol"]] = Decimal(row["last_price"])
    except Exception:
        pass
    for s in symbols:
        prices.setdefault(s, Decimal(SEED_PRICES[s]))
    return prices


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(SYMBOLS),
                        help="comma-separated, default all 8")
    parser.add_argument("--minutes", type=float, default=0,
                        help="stop after N minutes (0 = run until Ctrl+C)")
    parser.add_argument("--speed", type=float, default=1.6,
                        help="seconds between trades; lower is busier")
    parser.add_argument("--quiet", action="store_true", help="only print a heartbeat")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    unknown = [s for s in symbols if s not in SYMBOLS]
    if unknown:
        print(f"unknown symbols: {unknown}")
        return 2

    with httpx.Client(base_url=GATEWAY, timeout=20.0) as client:
        try:
            client.get("/health").raise_for_status()
        except Exception:
            print(f"Cannot reach the gateway at {GATEWAY}. Is `docker compose up -d` running?")
            return 2

        print("logging in traders...")
        traders = []
        for i in range(TRADER_COUNT):
            t = Trader(client, i)
            if t.login():
                traders.append(t)
        if len(traders) < 2:
            print("Need at least 2 seeded traders. Run: python infra/seed/seed_demo_data.py")
            return 2
        print(f"  {len(traders)} traders ready")

        fair = current_prices(client, symbols)
        anchor = dict(fair)
        print("starting prices: " + "  ".join(f"{s}={fair[s]:.2f}" for s in symbols))
        print(f"trading every ~{args.speed}s across {len(symbols)} symbols. Ctrl+C to stop.\n")

        deadline = time.monotonic() + args.minutes * 60 if args.minutes else None
        step = 0
        trades = 0
        rejected = 0
        started = time.monotonic()

        while _running:
            if deadline and time.monotonic() > deadline:
                break
            step += 1
            symbol = symbols[step % len(symbols)]

            # ---- random walk, tethered so it cannot wander off the chart ----
            shock = random.gauss(0, DRIFT)
            pull = float((anchor[symbol] - fair[symbol]) / anchor[symbol]) * 0.05
            fair[symbol] *= Decimal(str(1 + shock + pull))
            low = anchor[symbol] * Decimal(str(1 - MAX_EXCURSION))
            high = anchor[symbol] * Decimal(str(1 + MAX_EXCURSION))
            fair[symbol] = min(max(fair[symbol], low), high)

            price = fair[symbol]
            quantity = random.choice([5, 10, 10, 15, 20, 25])
            seller, buyer = random.sample(traders, 2)

            # ---- print a trade: a resting sell, then a buy that crosses it ---
            # The trade executes at the SELLER's price (price improvement for
            # the buyer), which is why the tape shows `price`, not the bid.
            sell_status = seller.place(symbol, "SELL", quantity, tick(price))
            if sell_status == 409:
                # That trader is out of inventory. Let them buy instead, which
                # rebalances who holds what over a long session.
                seller.place(symbol, "BUY", quantity, tick(price * Decimal("1.002")))
                rejected += 1
            elif sell_status in (200, 201):
                buy_status = buyer.place(symbol, "BUY", quantity, tick(price * Decimal("1.002")))
                if buy_status in (200, 201):
                    trades += 1
                else:
                    rejected += 1
            else:
                rejected += 1

            # ---- keep the book two-sided and deep enough to look real -------
            if step % 3 == 0:
                maker = random.choice(traders)
                depth = random.randint(1, 4)
                maker.place(symbol, "BUY", random.choice([20, 40, 50]),
                            tick(price * Decimal(str(1 - 0.001 * depth))))
                maker.place(symbol, "SELL", random.choice([20, 40, 50]),
                            tick(price * Decimal(str(1 + 0.001 * depth))))

            # ---- top the buyers up so nobody runs out of cash mid-demo ------
            if step % TOP_UP_EVERY == 0:
                for t in traders:
                    t.deposit()

            if not args.quiet:
                print(f"  [{step:>4}] {symbol:<6} {quantity:>3} @ {tick(price):>10}   "
                      f"trades={trades} rejected={rejected}")
            elif step % 25 == 0:
                mins = (time.monotonic() - started) / 60
                print(f"  {mins:5.1f}m  steps={step}  trades={trades}  rejected={rejected}")

            time.sleep(args.speed)

        elapsed = (time.monotonic() - started) / 60
        print(f"\nran {elapsed:.1f} minutes - {step} steps, {trades} trades printed, "
              f"{rejected} orders rejected (normal: inventory and cash limits)")
        print("final prices: " + "  ".join(f"{s}={fair[s]:.2f}" for s in symbols))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
