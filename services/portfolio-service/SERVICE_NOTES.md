# Portfolio Service Notes

- Port: 8006
- Database: `portfolio_db`

### Idempotency Model
- Implements **database-first idempotency** to prevent redelivered trades from double-counting.
- The Redis marker (`idem:portfolio:{event_id}`) is used as a fast read-only check to avoid unnecessary database connections for events already processed.
- The ultimate source of truth is the `processed_events` table, which is inserted within the exact same database transaction as the holdings update. The Redis marker is only set *after* a successful commit.

### Accounting
- Computes weighted average cost for BUY fills.
- Computes realized P&L for SELL fills against the average cost.
- P&L and Holdings updates are designed following CQRS principles—driven asynchronously by `trade.executed` events.
