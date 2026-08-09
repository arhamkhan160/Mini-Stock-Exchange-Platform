import sys
import os
import time
import uuid
import random
import asyncio
from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from libs.common.security import create_access_token, get_current_user, CurrentUser
from libs.common.money import money_str, to_money
from libs.common.symbols import SYMBOLS, SEED_PRICES

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mock Data Storage
USERS = {}
BALANCES = {}
TRANSACTIONS = {}
ORDERS = {}
PORTFOLIOS = {}
NOTIFICATIONS = {}

class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str
    full_name: Optional[str] = None

@app.post("/api/auth/register")
def register(req: RegisterRequest):
    user_id = uuid.uuid4()
    USERS[str(user_id)] = {"id": str(user_id), "email": req.email, "username": req.username}
    BALANCES[str(user_id)] = {"available": "10000.0000", "reserved": "0.0000", "total": "10000.0000", "currency": "USD"}
    TRANSACTIONS[str(user_id)] = []
    ORDERS[str(user_id)] = []
    PORTFOLIOS[str(user_id)] = []
    NOTIFICATIONS[str(user_id)] = []
    
    token = create_access_token(user_id, req.email, req.username)
    return {"access_token": token, "token_type": "bearer", "user": USERS[str(user_id)]}

@app.post("/api/auth/login")
def login(username: str = Form(...), password: str = Form(...)):
    user = next((u for u in USERS.values() if u["email"] == username or u["username"] == username), None)
    if not user:
        # Auto-create if not exists for easy testing
        req = RegisterRequest(email=username, username=username, password=password)
        return register(req)
    token = create_access_token(uuid.UUID(user["id"]), user["email"], user["username"])
    return {"access_token": token, "token_type": "bearer", "user": user}

@app.get("/api/users/me")
def get_me(current_user: CurrentUser = Depends(get_current_user)):
    return {"id": str(current_user.id), "email": current_user.email, "username": current_user.username}

@app.get("/api/account/balance")
def get_balance(current_user: CurrentUser = Depends(get_current_user)):
    return BALANCES.get(str(current_user.id), {"available": "0.0000", "reserved": "0.0000", "total": "0.0000", "currency": "USD"})

class DepositRequest(BaseModel):
    amount: str

@app.post("/api/account/deposit")
def deposit(req: DepositRequest, current_user: CurrentUser = Depends(get_current_user)):
    b = BALANCES[str(current_user.id)]
    new_avail = to_money(b["available"]) + to_money(req.amount)
    b["available"] = money_str(new_avail)
    b["total"] = money_str(new_avail + to_money(b["reserved"]))
    TRANSACTIONS[str(current_user.id)].insert(0, {
        "id": str(uuid.uuid4()), "type": "DEPOSIT", "amount": money_str(req.amount), 
        "currency": "USD", "status": "COMPLETED", "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    })
    return b

@app.get("/api/account/transactions")
def get_transactions(limit: int = 50, offset: int = 0, current_user: CurrentUser = Depends(get_current_user)):
    txs = TRANSACTIONS.get(str(current_user.id), [])
    return txs[offset:offset+limit]

class OrderRequest(BaseModel):
    symbol: str
    side: str
    order_type: str
    price: Optional[str] = None
    quantity: int
    client_order_id: Optional[str] = None

@app.post("/api/orders")
async def place_order(req: OrderRequest, current_user: CurrentUser = Depends(get_current_user)):
    order_id = str(uuid.uuid4())
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    order = {
        "id": order_id, "user_id": str(current_user.id), "symbol": req.symbol,
        "side": req.side, "order_type": req.order_type, "price": req.price,
        "quantity": req.quantity, "filled_quantity": 0, "status": "OPEN",
        "created_at": now, "updated_at": now
    }
    ORDERS[str(current_user.id)].insert(0, order)
    
    # Simulate async fill after 2s
    async def fill_order():
        await asyncio.sleep(2)
        order["status"] = "FILLED"
        order["filled_quantity"] = order["quantity"]
        order["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        fill_price = req.price or SEED_PRICES.get(req.symbol, "100.0000")
        
        uid = str(current_user.id)
        pf = PORTFOLIOS[uid]
        holding = next((h for h in pf if h["symbol"] == req.symbol), None)
        if req.side == "BUY":
            cost = to_money(fill_price) * order["quantity"]
            BALANCES[uid]["available"] = money_str(to_money(BALANCES[uid]["available"]) - cost)
            if holding:
                new_qty = holding["quantity"] + order["quantity"]
                old_cost = to_money(holding["avg_cost"]) * holding["quantity"]
                holding["avg_cost"] = money_str((old_cost + cost) / new_qty)
                holding["quantity"] = new_qty
                holding["available_quantity"] += order["quantity"]
            else:
                pf.append({
                    "symbol": req.symbol, "name": SYMBOLS.get(req.symbol, req.symbol),
                    "quantity": order["quantity"], "available_quantity": order["quantity"],
                    "reserved_quantity": 0, "avg_cost": money_str(fill_price),
                    "last_price": money_str(fill_price), "market_value": money_str(cost),
                    "unrealized_pnl": "0.0000", "realized_pnl": "0.0000", "price_stale": False
                })
        elif req.side == "SELL" and holding:
            holding["quantity"] -= order["quantity"]
            holding["available_quantity"] -= order["quantity"]
            realized = (to_money(fill_price) - to_money(holding["avg_cost"])) * order["quantity"]
            holding["realized_pnl"] = money_str(to_money(holding["realized_pnl"]) + realized)
            revenue = to_money(fill_price) * order["quantity"]
            BALANCES[uid]["available"] = money_str(to_money(BALANCES[uid]["available"]) + revenue)
            if holding["quantity"] == 0:
                holding["avg_cost"] = "0.0000"

        NOTIFICATIONS[uid].insert(0, {
            "id": str(uuid.uuid4()), "type": "ORDER_FILLED",
            "title": "Order Filled", "message": f"{req.side} {order['quantity']} {req.symbol} @ {fill_price}",
            "is_read": False, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "data": {"order_id": order_id}
        })

    asyncio.create_task(fill_order())
    return order

@app.get("/api/orders")
def list_orders(limit: int = 50, offset: int = 0, current_user: CurrentUser = Depends(get_current_user)):
    return ORDERS.get(str(current_user.id), [])[offset:offset+limit]

@app.get("/api/orders/{id}")
def get_order(id: str, current_user: CurrentUser = Depends(get_current_user)):
    for o in ORDERS.get(str(current_user.id), []):
        if o["id"] == id:
            return o
    raise HTTPException(status_code=404, detail="Order not found")

@app.delete("/api/orders/{id}")
def cancel_order(id: str, current_user: CurrentUser = Depends(get_current_user)):
    for o in ORDERS.get(str(current_user.id), []):
        if o["id"] == id:
            if o["status"] != "OPEN":
                raise HTTPException(status_code=400, detail="Cannot cancel")
            o["status"] = "CANCELLED"
            return {"status": "CANCELLED"}
    raise HTTPException(status_code=404, detail="Order not found")

@app.get("/api/book/{symbol}")
def get_book(symbol: str, depth: int = 10):
    price = to_money(SEED_PRICES.get(symbol.upper(), "100.0000"))
    bids = [{"price": money_str(price - to_money(f"{0.01*i}")), "quantity": 100} for i in range(1, depth+1)]
    asks = [{"price": money_str(price + to_money(f"{0.01*i}")), "quantity": 100} for i in range(1, depth+1)]
    return {"symbol": symbol.upper(), "bids": bids, "asks": asks, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

@app.get("/api/market/symbols")
def get_symbols():
    return [
        {
            "symbol": sym, "name": name, "last_price": SEED_PRICES.get(sym, "100.0000"),
            "change": "1.5000", "change_pct": 1.2, "volume_24h": 10000
        }
        for sym, name in SYMBOLS.items()
    ]

@app.get("/api/market/quote/{symbol}")
def get_quote(symbol: str):
    return {
        "symbol": symbol.upper(), "price": SEED_PRICES.get(symbol.upper(), "100.0000"),
        "quantity": 100, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }

@app.get("/api/market/candles/{symbol}")
def get_candles(symbol: str, interval: str = "1m", limit: int = 300):
    candles = []
    now = int(time.time())
    start = now - (limit * 60)
    price = float(SEED_PRICES.get(symbol.upper(), "100.00"))
    for i in range(limit):
        price += random.uniform(-0.5, 0.5)
        candles.append({
            "time": start + (i * 60),
            "open": round(price, 4),
            "high": round(price + 0.5, 4),
            "low": round(price - 0.5, 4),
            "close": round(price + random.uniform(-0.1, 0.1), 4),
            "volume": random.randint(10, 1000)
        })
    return candles

@app.get("/api/market/trades/{symbol}")
def get_market_trades(symbol: str, limit: int = 30):
    return []

@app.get("/api/portfolio")
def get_portfolio(current_user: CurrentUser = Depends(get_current_user)):
    h = PORTFOLIOS.get(str(current_user.id), [])
    return {
        "user_id": str(current_user.id),
        "holdings": h,
        "totals": {
            "total_market_value": "0.0000",
            "total_cost_basis": "0.0000",
            "total_unrealized_pnl": "0.0000",
            "total_realized_pnl": "0.0000",
            "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
    }

@app.get("/api/portfolio/pnl")
def get_pnl(current_user: CurrentUser = Depends(get_current_user)):
    return {
        "total_market_value": "0.0000",
        "total_cost_basis": "0.0000",
        "total_unrealized_pnl": "0.0000",
        "total_realized_pnl": "0.0000",
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }

@app.get("/api/notifications")
def get_notifications(limit: int = 20, unread_only: bool = False, current_user: CurrentUser = Depends(get_current_user)):
    n = NOTIFICATIONS.get(str(current_user.id), [])
    if unread_only:
        n = [x for x in n if not x["is_read"]]
    return n[:limit]

@app.get("/api/notifications/unread-count")
def unread_count(current_user: CurrentUser = Depends(get_current_user)):
    n = NOTIFICATIONS.get(str(current_user.id), [])
    return {"count": sum(1 for x in n if not x["is_read"])}

@app.post("/api/notifications/{id}/read")
def mark_read(id: str, current_user: CurrentUser = Depends(get_current_user)):
    for x in NOTIFICATIONS.get(str(current_user.id), []):
        if x["id"] == id:
            x["is_read"] = True
            break
    return {}

@app.post("/api/notifications/read-all")
def mark_all_read(current_user: CurrentUser = Depends(get_current_user)):
    c = 0
    for x in NOTIFICATIONS.get(str(current_user.id), []):
        if not x["is_read"]:
            x["is_read"] = True
            c += 1
    return {"marked": c}

@app.websocket("/ws/market")
async def websocket_market(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await asyncio.sleep(1)
            sym = random.choice(list(SYMBOLS.keys()))
            price = money_str(to_money(SEED_PRICES[sym]) + to_money(str(random.uniform(-1, 1))))
            await ws.send_json({
                "type": "tick", "symbol": sym, "price": price, 
                "quantity": random.randint(1, 100), "ts": int(time.time())
            })
    except WebSocketDisconnect:
        pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
