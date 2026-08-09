"""Market-maker bot: registers a trading account and fires random LIMIT orders
through the gateway, exactly like a real frontend user would. Needs Order
Service running to actually accept anything — until then every POST /orders
just 404s/503s, which this script logs and keeps going through.

Usage:
    python infra/seed/market_maker.py            # place a batch of orders, then exit
    python infra/seed/market_maker.py --loop      # keep placing orders forever (Ctrl+C to stop)
"""

import argparse
import random
import sys
import time
import uuid

import httpx

API_BASE = "http://localhost:8000"

# Mirrors libs/common/symbols.py — kept as a local literal so this script has
# no dependency on the `common` package and can run with nothing but httpx.
SEED_PRICES = {
    "AAPL": 195.50,
    "GOOGL": 175.20,
    "MSFT": 425.80,
    "AMZN": 185.40,
    "TSLA": 245.60,
    "NVDA": 128.30,
    "META": 512.10,
    "NFLX": 685.90,
}

DEPOSIT_CHUNK = "1000000.00"  # matches the account-service per-call deposit cap
DEPOSIT_CHUNKS = 10  # 10 * 1,000,000 = plenty of buying power for the bot


def wait_for_gateway(client: httpx.Client, retries: int = 30, delay: float = 2.0) -> bool:
    for attempt in range(1, retries + 1):
        try:
            if client.get("/health").status_code == 200:
                return True
        except httpx.TransportError:
            pass
        print(f"waiting for gateway ({attempt}/{retries})...")
        time.sleep(delay)
    return False


def register_bot(client: httpx.Client) -> dict:
    tag = str(uuid.uuid4())[:8]
    username = f"mm_bot_{tag}"
    res = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@mse.local",
            "password": "market-maker-bot-pw",
            "full_name": "Market Maker Bot",
        },
    )
    res.raise_for_status()
    print(f"registered bot {username}")
    return res.json()


def fund_bot(client: httpx.Client, headers: dict) -> None:
    for _ in range(DEPOSIT_CHUNKS):
        client.post("/api/account/deposit", headers=headers, json={"amount": DEPOSIT_CHUNK})
    print(f"deposited {int(float(DEPOSIT_CHUNK) * DEPOSIT_CHUNKS):,} to the bot wallet")


def place_random_order(client: httpx.Client, headers: dict) -> None:
    symbol = random.choice(list(SEED_PRICES))
    side = random.choice(["BUY", "SELL"])
    base_price = SEED_PRICES[symbol]
    price = round(base_price * (1 + random.uniform(-0.01, 0.01)), 2)
    qty = random.randint(1, 50)

    try:
        res = client.post(
            "/api/orders",
            headers=headers,
            json={"symbol": symbol, "side": side, "order_type": "LIMIT", "price": f"{price:.4f}", "quantity": qty},
        )
        status = "ACCEPTED" if res.status_code == 201 else f"FAILED ({res.status_code})"
    except httpx.TransportError as exc:
        status = f"FAILED ({exc})"
    print(f"[{symbol}] {side} {qty} @ ${price:.2f} -> {status}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="place orders forever instead of a fixed batch")
    parser.add_argument("--count", type=int, default=40, help="orders to place in one-shot mode")
    parser.add_argument("--interval", type=float, default=1.5, help="seconds between orders")
    args = parser.parse_args()

    with httpx.Client(base_url=API_BASE, timeout=10.0) as client:
        if not wait_for_gateway(client):
            print("gateway never came up — giving up")
            sys.exit(1)

        bot = register_bot(client)
        headers = {"Authorization": f"Bearer {bot['access_token']}"}
        fund_bot(client, headers)

        print(f"placing orders ({'forever' if args.loop else args.count})...")
        placed = 0
        try:
            while args.loop or placed < args.count:
                place_random_order(client, headers)
                placed += 1
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nmarket maker stopped.")


if __name__ == "__main__":
    main()
