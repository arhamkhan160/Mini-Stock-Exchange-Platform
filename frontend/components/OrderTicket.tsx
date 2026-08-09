"use client";

// ============================================================================
// OWNER: TEAM C (Abidur Rahman Asif).
// This is a COMPILING STUB so Team D's /market/[symbol] page builds from hour 0.
// Team C replaces the body. DO NOT change the file path or the props — Team D
// imports it exactly like this.
//
//   <OrderTicket symbol="AAPL" lastPrice="195.5000" onPlaced={refresh} />
//
// Real version must have: BUY/SELL toggle, LIMIT/MARKET toggle, quantity,
// price (hidden for MARKET), estimated cost, available cash / available shares,
// submit disabled while in flight, client_order_id for double-click safety,
// and the backend `detail` shown verbatim on 4xx.
// ============================================================================

import { Card } from "./ui";

export interface OrderTicketProps {
  symbol: string;
  lastPrice: string | null;
  /** Called after an order is accepted, so the parent can refresh lists. */
  onPlaced?: () => void;
}

export default function OrderTicket({ symbol, lastPrice }: OrderTicketProps) {
  return (
    <Card title={`Trade ${symbol}`}>
      <p className="text-sm text-muted">
        Order ticket not implemented yet (Team C). Last price:{" "}
        <span className="num text-ink">{lastPrice ?? "—"}</span>
      </p>
    </Card>
  );
}
