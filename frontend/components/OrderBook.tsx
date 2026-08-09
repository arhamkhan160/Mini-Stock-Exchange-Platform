"use client";

// ============================================================================
// OWNER: TEAM C (Abidur Rahman Asif).
// COMPILING STUB so Team D's /market/[symbol] page builds from hour 0.
// Team C replaces the body. DO NOT change the file path or the props.
//
//   <OrderBook symbol="AAPL" onPriceClick={(p) => setPrice(p)} />
//
// Real version: poll GET /api/book/{symbol}?depth=10 every 2s, asks descending
// on top, bids descending below, aggregated per price level, depth bars sized
// by cumulative quantity, spread in the middle, click a row to fill the ticket.
// ============================================================================

import { Card, Empty } from "./ui";

export interface OrderBookProps {
  symbol: string;
  /** Clicking a price level should prefill the order ticket. */
  onPriceClick?: (price: string) => void;
}

export default function OrderBook({ symbol }: OrderBookProps) {
  return (
    <Card title="Order book">
      <Empty title="Order book not implemented yet (Team C)" hint={symbol} />
    </Card>
  );
}
