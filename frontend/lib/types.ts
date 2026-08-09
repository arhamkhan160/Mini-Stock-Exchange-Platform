// Mirrors the gateway contract exactly. If a field name here disagrees with the
// backend, the backend wins — fix this file, not the backend.
// REMINDER: every money value is a STRING with 4 decimals ("195.5000").
// The ONLY exception is /api/market/candles, which returns numbers for the chart.

export type Side = "BUY" | "SELL";
export type OrderType = "LIMIT" | "MARKET";

export type OrderStatus =
  | "PENDING"
  | "NEW"
  | "PARTIALLY_FILLED"
  | "FILLED"
  | "CANCEL_PENDING"
  | "CANCELLED"
  | "REJECTED";

export interface User {
  id: string;
  email: string;
  username: string;
  full_name: string | null;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface Balance {
  user_id: string;
  cash_balance: string;
  held_balance: string;
  available_balance: string;
  currency: string;
}

export interface Transaction {
  id: string;
  type: "DEPOSIT" | "HOLD" | "RELEASE" | "TRADE_BUY" | "TRADE_SELL";
  amount: string;
  balance_after: string;
  reference_id: string | null;
  description: string | null;
  created_at: string;
}

export interface Order {
  id: string;
  user_id: string;
  symbol: string;
  side: Side;
  order_type: OrderType;
  price: string | null;
  quantity: number;
  filled_quantity: number;
  avg_fill_price: string;
  status: OrderStatus;
  reject_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface BookLevel {
  price: string;
  quantity: number;
  orders: number;
}

export interface OrderBookSnapshot {
  symbol: string;
  bids: BookLevel[];
  asks: BookLevel[];
  best_bid: string | null;
  best_ask: string | null;
  spread: string | null;
  ts: string;
}

export interface MarketTrade {
  trade_id: string;
  symbol: string;
  price: string;
  quantity: number;
  aggressor_side: Side;
  executed_at: string;
}

export interface SymbolQuote {
  symbol: string;
  name: string;
  last_price: string;
  change: string;
  change_pct: number;
  volume_24h?: number;
  stale?: boolean;
}

/** /api/market/candles — NUMBERS, not strings. `time` is epoch SECONDS. */
export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Holding {
  symbol: string;
  name: string;
  quantity: number;
  available_quantity: number;
  reserved_quantity: number;
  avg_cost: string;
  last_price: string;
  market_value: string;
  unrealized_pnl: string;
  realized_pnl: string;
  price_stale: boolean;
}

export interface Portfolio {
  user_id: string;
  holdings: Holding[];
  totals: {
    total_market_value: string;
    total_cost_basis: string;
    total_unrealized_pnl: string;
    total_realized_pnl: string;
  };
}

export interface Notification {
  id: string;
  type: "ORDER_FILLED" | "ORDER_PARTIALLY_FILLED" | "ORDER_CANCELLED" | "ORDER_REJECTED";
  title: string;
  message: string;
  symbol: string | null;
  reference_id: string | null;
  is_read: boolean;
  created_at: string;
}

export interface Tick {
  type: "tick";
  symbol: string;
  price: string;
  quantity: number;
  ts: number;
}
