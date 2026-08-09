"""Market-maker bot: quotes both sides of the book for every symbol through
the gateway, exactly like a real frontend user would — this doubles as an
end-to-end integration test, and it's what makes the demo look alive (prices
tick, candles move, fills generate notifications).

Fixed identities (idempotent — re-running logs in instead of re-registering):
    bot1..bot4 @mse.local / BotPassw0rd!   (market makers, $1,000,000 each)
    demo@mse.local / DemoPassw0rd!          (a human-shaped account, $100,000)

Each bot quotes 2 of the 8 symbols (round-robin), 5 bid levels + 5 ask
levels, 50 shares each, in 0.1% steps off the last price. Rate limit: POST
/api/orders is 30/min PER IDENTITY (gateway §1.6/1.8). 4 bots x 2 symbols x
10 orders = 20 orders/bot/cycle; quotes are paced at ~1 every 2.2s (< 30/min
with margin) rather than literally every 30s, which 20 orders/cycle would
blow through — see PACE_SECONDS below.

Usage:
    python infra/seed/market_maker.py            # one bootstrap + one quoting pass, then exit
    python infra/seed/market_maker.py --loop      # keep re-quoting forever (Ctrl+C to stop)
"""

import argparse
import random
import sys
import time

import httpx

API_BASE = "http://localhost:8000"

SEED_PRICES = {
    "AAPL": 195.50, "GOOGL": 175.20, "MSFT": 425.80, "AMZN": 185.40,
    "TSLA": 245.60, "NVDA": 128.30, "META": 512.10, "NFLX": 685.90,
}
SYMBOLS = list(SEED_PRICES)

BOTS = [{"username": f"bot{i}", "email": f"bot{i}@mse.local", "password": "BotPassw0rd!"} for i in range(1, 5)]
DEMO = {"username": "demo", "email": "demo@mse.local", "password": "DemoPassw0rd!"}

BOT_DEPOSIT = "1000000.00"
DEMO_DEPOSIT = "100000.00"

LEVELS = 5           # bid levels and ask levels per symbol
LEVEL_STEP = 0.001    # 0.1% per level
QTY_PER_LEVEL = 50

# 20 orders/bot/cycle at this pace = ~44s/cycle, well under 30/min per identity.
PACE_SECONDS = 2.2


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


def register_or_login(client: httpx.Client, identity: dict) -> dict | None:
    r = client.post(
        "/api/auth/register",
        json={
            "username": identity["username"],
            "email": identity["email"],
            "password": identity["password"],
            "full_name": identity["username"].replace("_", " ").title(),
        },
    )
    if r.status_code == 201:
        print(f"registered {identity['username']}")
        return r.json()
    if r.status_code == 409:
        r = client.post("/api/auth/login", data={"username": identity["username"], "password": identity["password"]})
        if r.status_code == 200:
            print(f"{identity['username']} already exists, logged in")
            return r.json()
    print(f"could not register or log in {identity['username']}: {r.status_code} {r.text}")
    return None


def deposit(client: httpx.Client, headers: dict, amount: str) -> None:
    client.post("/api/account/deposit", headers=headers, json={"amount": amount})


def place(client: httpx.Client, headers: dict, symbol: str, side: str, quantity: int, price: float) -> httpx.Response:
    return client.post(
        "/api/orders",
        headers=headers,
        json={"symbol": symbol, "side": side, "order_type": "LIMIT", "price": f"{price:.4f}", "quantity": quantity},
    )


def bootstrap_inventory(client: httpx.Client, bots: list[dict]) -> None:
    """Give bot1..bot4 initial shares so they can quote asks (a SELL with no
    owned shares is correctly rejected — see the shared contract). Crosses a
    small trade between adjacent bots for every symbol. On a completely fresh
    system where NO account has ever held shares, the sell leg is expected to
    be rejected (409) — that's not a bug here, it means Portfolio/Order
    haven't been seeded with any prior holdings yet. We log and move on; the
    quoting loop still posts bids, and asks simply start succeeding on their
    own the moment any bot accumulates real inventory from a filled buy."""
    print("bootstrapping inventory (best-effort — a fresh system may reject every sell here, and that's fine)...")
    for i, symbol in enumerate(SYMBOLS):
        seller = bots[i % len(bots)]
        buyer = bots[(i + 1) % len(bots)]
        price = SEED_PRICES[symbol]
        r_sell = place(client, seller["headers"], symbol, "SELL", 20, price)
        if r_sell.status_code != 201:
            print(f"  [{symbol}] seed sell by {seller['identity']} rejected ({r_sell.status_code}) — skipping")
            continue
        r_buy = place(client, buyer["headers"], symbol, "BUY", 20, price)
        status = "crossed" if r_buy.status_code == 201 else f"buy failed ({r_buy.status_code})"
        print(f"  [{symbol}] {seller['identity']} -> {buyer['identity']} @ {price:.2f}: {status}")
        time.sleep(PACE_SECONDS)


def quote_symbol(client: httpx.Client, bot: dict, symbol: str, cross: bool) -> None:
    base = SEED_PRICES[symbol]
    for level in range(1, LEVELS + 1):
        bid_price = round(base * (1 - LEVEL_STEP * level), 2)
        r = place(client, bot["headers"], symbol, "BUY", QTY_PER_LEVEL, bid_price)
        print(f"[{bot['identity']}] {symbol} BID {QTY_PER_LEVEL} @ {bid_price:.2f} -> {r.status_code}")
        time.sleep(PACE_SECONDS)

        ask_price = round(base * (1 + LEVEL_STEP * level), 2)
        r = place(client, bot["headers"], symbol, "SELL", QTY_PER_LEVEL, ask_price)
        print(f"[{bot['identity']}] {symbol} ASK {QTY_PER_LEVEL} @ {ask_price:.2f} -> {r.status_code}")
        time.sleep(PACE_SECONDS)

    if cross:
        # Occasionally cross the spread so a trade actually prints — a book
        # full of resting quotes with nothing filling makes for a dead demo.
        r = place(client, bot["headers"], symbol, "BUY", QTY_PER_LEVEL, round(base * (1 + LEVEL_STEP), 2))
        print(f"[{bot['identity']}] {symbol} crossing buy -> {r.status_code}")
        time.sleep(PACE_SECONDS)


def run_cycle(client: httpx.Client, bots: list[dict], cross: bool) -> None:
    for i, bot in enumerate(bots):
        my_symbols = SYMBOLS[i::len(bots)]  # round-robin: 2 symbols/bot for 4 bots x 8 symbols
        for symbol in my_symbols:
            quote_symbol(client, bot, symbol, cross=cross and random.random() < 0.5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="keep re-quoting forever instead of a single pass")
    args = parser.parse_args()

    with httpx.Client(base_url=API_BASE, timeout=10.0) as client:
        if not wait_for_gateway(client):
            print("gateway never came up — giving up")
            sys.exit(1)

        bots = []
        for identity in BOTS:
            auth = register_or_login(client, identity)
            if auth is None:
                continue
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            deposit(client, headers, BOT_DEPOSIT)
            bots.append({"identity": identity["username"], "headers": headers})

        demo_auth = register_or_login(client, DEMO)
        if demo_auth is not None:
            deposit(client, {"Authorization": f"Bearer {demo_auth['access_token']}"}, DEMO_DEPOSIT)

        if len(bots) < 2:
            print("fewer than 2 bots came up — can't seed inventory or quote both sides. Exiting.")
            sys.exit(1)

        bootstrap_inventory(client, bots)

        print(f"quoting {len(SYMBOLS)} symbols across {len(bots)} bots ({'looping' if args.loop else 'one pass'})...")
        try:
            while True:
                run_cycle(client, bots, cross=True)
                if not args.loop:
                    break
        except KeyboardInterrupt:
            print("\nmarket maker stopped.")


if __name__ == "__main__":
    main()
