# Sequence: Place Order (the saga)

## Happy path — limit buy that matches

```mermaid
sequenceDiagram
    autonumber
    actor T as Trader
    participant FE as Frontend
    participant GW as Gateway
    participant OS as Order
    participant AC as Account
    participant MQ as RabbitMQ
    participant ME as Matching Engine
    participant PF as Portfolio
    participant MD as Market Data
    participant NT as Notification

    T->>FE: Buy 10 AAPL @ 195.50
    FE->>GW: POST /api/orders (JWT)
    GW->>GW: validate JWT, rate limit (30/min)
    GW->>OS: POST /orders

    OS->>OS: validate symbol, side, type, qty, tick size
    OS->>OS: persist order as PENDING (commit)

    rect rgb(232, 236, 255)
    note over OS,AC: UC7 — Validate Buying Power
    OS->>AC: POST /internal/reservations {order_id, amount 1955.00}
    AC->>AC: acquire lock:funds:{user}, SELECT ... FOR UPDATE
    AC->>AC: available >= amount? held_balance += amount
    AC-->>OS: 201 HELD
    end

    OS->>OS: PENDING -> NEW (commit)
    OS->>MQ: publish order.accepted
    OS-->>GW: 201 order
    GW-->>FE: 201 order

    MQ-->>ME: order.accepted
    rect rgb(232, 236, 255)
    note over ME: UC8 — price–time priority match
    ME->>ME: match against asks, trade at the RESTING price
    ME->>MQ: publish trade.executed (one per fill)
    end

    par settlement fan-out
        MQ-->>OS: trade.executed
        OS->>OS: filled_quantity += qty, status -> FILLED
    and
        MQ-->>AC: trade.executed
        AC->>AC: buyer: cash -= notional, consume hold, release over-reservation
        AC->>AC: seller: cash += notional
    and
        MQ-->>PF: trade.executed
        PF->>PF: buyer: qty += n, recompute avg cost
        PF->>PF: seller: qty -= n, realize P&L, consume share reservation
    and
        MQ-->>MD: trade.executed
        MD->>MD: insert trade, upsert 1m + 5m candles (primary)
        MD->>MD: set md:last_price, PUBLISH md:ticks
    and
        MQ-->>NT: trade.executed
        NT->>NT: notification per side + mock email
    end

    MD-->>FE: WebSocket tick (via gateway bridge)
    FE-->>T: price, portfolio and alert update live
```

**Note on step ordering.** The order is persisted as `PENDING` *before* the
reservation, because the reservation is idempotent on `order_id` and therefore
needs the id to exist first. That is what makes a retried call safe.

**Note on the trade price.** The trade executes at the **resting** order's
price, so an aggressor bidding 196.00 against a resting ask of 195.50 pays
195.50. The buyer reserved against 196.00, so the difference stays held until
the order finishes and is then released — which is why `buy_order_limit_price`
travels in the event.

## Failure path — compensating actions

```mermaid
sequenceDiagram
    autonumber
    participant FE as Frontend
    participant OS as Order
    participant AC as Account
    participant PF as Portfolio
    participant MQ as RabbitMQ

    FE->>OS: POST /orders

    alt insufficient buying power
        OS->>AC: POST /internal/reservations
        AC-->>OS: 409 insufficient buying power
        OS->>OS: order -> REJECTED (reason recorded)
        OS->>MQ: publish order.rejected
        OS-->>FE: 409 {"detail": "insufficient buying power"}

    else account service unreachable
        OS->>AC: POST /internal/reservations
        AC--xOS: timeout / 503
        note over OS,AC: the first call MAY have succeeded — compensate blindly
        OS->>AC: POST /internal/reservations/{id}/release
        OS->>PF: POST /internal/share-reservations/{id}/release
        OS->>OS: order -> REJECTED (SERVICE_UNAVAILABLE)
        OS-->>FE: 503

    else broker unavailable after a successful reservation
        OS->>AC: POST /internal/reservations
        AC-->>OS: 201 HELD
        OS->>MQ: publish order.accepted
        MQ--xOS: publish failed
        rect rgb(255, 235, 235)
        note over OS,PF: COMPENSATING ACTION — funds must not stay locked
        OS->>AC: release reservation
        OS->>PF: release share reservation
        end
        OS->>OS: order -> REJECTED (BROKER_UNAVAILABLE)
        OS-->>FE: 503
    end
```

Both release endpoints are **idempotent and safe when nothing was reserved** —
they return `released: 0` rather than 404 — so the Order Service can compensate
blindly without first working out how far it got.

## Cancellation — two-phase, because the book is the authority

```mermaid
sequenceDiagram
    autonumber
    actor T as Trader
    participant OS as Order
    participant MQ as RabbitMQ
    participant ME as Matching Engine
    participant AC as Account
    participant NT as Notification

    T->>OS: DELETE /orders/{id}
    OS->>OS: owner? cancellable? -> status CANCEL_PENDING
    OS->>MQ: publish order.cancel_requested
    OS-->>T: 202 {"status": "CANCEL_PENDING"}

    MQ-->>ME: order.cancel_requested
    ME->>ME: acquire the per-symbol lock

    alt still resting in the book
        ME->>ME: remove from the price level and the index
        ME->>MQ: publish order.cancelled {cancelled_quantity}
        MQ-->>OS: status -> CANCELLED
        MQ-->>AC: release the remaining reservation
        MQ-->>NT: "Order cancelled"
    else already filled or never in the book
        ME->>MQ: publish order.cancel_rejected {ALREADY_FILLED}
        MQ-->>OS: revert CANCEL_PENDING -> FILLED
    end
```

**Why not let the Order Service publish `order.cancelled` directly?** Only the
book knows whether the order was still resting when the cancel arrived. If funds
were released optimistically, a fill landing in the same millisecond would
settle against a reservation that no longer exists. Routing the cancel through
the engine — where it takes the same per-symbol lock as matching — makes the two
strictly ordered and the compensation race-free.
