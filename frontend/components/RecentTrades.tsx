"use client";

// ============================================================================
// OWNER: TEAM C (Abidur Rahman Asif).
// COMPILING STUB so Team D's /market/[symbol] page builds from hour 0.
// Team C replaces the body. DO NOT change the file path or the props.
//
//   <RecentTrades symbol="AAPL" />
//
// Real version: GET /api/market/trades/{symbol}?limit=30 on mount, then prepend
// live trades as ticks arrive; price coloured by aggressor_side, time HH:MM:SS.
// ============================================================================

import { Card, Empty } from "./ui";

export interface RecentTradesProps {
  symbol: string;
}

export default function RecentTrades({ symbol }: RecentTradesProps) {
  return (
    <Card title="Recent trades">
      <Empty title="Trade tape not implemented yet (Team C)" hint={symbol} />
    </Card>
  );
}
