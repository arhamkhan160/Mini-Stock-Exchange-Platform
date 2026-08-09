# Market Data Service Notes

- Port: 8005
- Database: `market_db` (primary and replica)

### Important Deviations
- **Candle API Output Type Deviation**: The `/market/candles` endpoint returns `open`, `high`, `low`, and `close` as **numbers** (float/int) rather than strings, and `time` as an **epoch-seconds int**. This is a deliberate deviation from the standard money formatting rule ("Money crosses the wire as a JSON string") because the `lightweight-charts` frontend library requires them in this specific format.

### Architecture
- Uses a read/write split. All writes (trade processing, candle upserts) go to the primary database (`WriteSession`), and all reads (chart loading, API responses) go to the replica (`ReadSession`), with a fallback to the primary if the replica is unavailable.
- Uses `trade.executed` events to build candlestick charts and broadcast live ticks to the frontend via WebSocket.
- Contains a Redis fan-out Pub/Sub system for WebSockets.
