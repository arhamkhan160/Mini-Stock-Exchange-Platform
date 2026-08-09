"""Stand-in for Team B's Account Service and Team D's Portfolio Service.

The Order Service's only outbound dependency is these four endpoints, so this
30-line fake is the difference between being blocked and not. Run it on 8002
and 8006 and point ACCOUNT_SERVICE_URL / PORTFOLIO_SERVICE_URL at it:

    uvicorn scripts.fake_reservations:app --port 8002 &
    uvicorn scripts.fake_reservations:app --port 8006 &

Thresholds are there so the 409 paths are reachable: an amount over 100000
gives "insufficient buying power", a quantity over 1000 gives
"insufficient shares".
"""

from fastapi import FastAPI, HTTPException

app = FastAPI(title="fake reservations (dev only)")
HELD: dict[str, dict] = {}
SHARES: dict[str, dict] = {}


@app.post("/internal/reservations", status_code=201)
async def reserve(body: dict):
    if body["order_id"] in HELD:            # idempotent on order_id, like the real one
        return HELD[body["order_id"]]
    if float(body["amount"]) > 100000:
        raise HTTPException(409, "insufficient buying power")
    HELD[body["order_id"]] = {"reservation_id": body["order_id"], **body, "status": "HELD"}
    return HELD[body["order_id"]]


@app.post("/internal/reservations/{oid}/release")
async def release(oid: str):
    held = HELD.pop(oid, None)
    return {"released": held["amount"] if held else "0.0000"}


@app.post("/internal/share-reservations", status_code=201)
async def reserve_shares(body: dict):
    if body["order_id"] in SHARES:
        return SHARES[body["order_id"]]
    if body["quantity"] > 1000:
        raise HTTPException(409, "insufficient shares")
    SHARES[body["order_id"]] = {"reservation_id": body["order_id"], **body, "status": "HELD"}
    return SHARES[body["order_id"]]


@app.post("/internal/share-reservations/{oid}/release")
async def release_shares(oid: str):
    held = SHARES.pop(oid, None)
    return {"released": held["quantity"] if held else 0}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "fake-reservations"}
