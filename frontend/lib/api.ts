// The ONLY place that talks to the gateway. Every page uses these helpers.
import type {
  AuthResponse, Balance, Candle, Notification, Order, OrderBookSnapshot,
  Portfolio, SymbolQuote, MarketTrade, Transaction, User,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

export const TOKEN_KEY = "mse_token";
export const USER_KEY = "mse_user";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null; // SSR guard — never touch localStorage on the server
  return window.localStorage.getItem(TOKEN_KEY);
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof URLSearchParams)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers || {}),
    },
    cache: "no-store",
  });

  if (res.status === 401) {
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(TOKEN_KEY);
      window.localStorage.removeItem(USER_KEY);
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
      }
    }
    throw new ApiError(401, "Your session expired. Please log in again.");
  }

  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = body.detail[0]?.msg ?? detail; // FastAPI 422
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 429) detail = "Too many requests — slow down and try again in a minute.";
    if (res.status === 503) detail = detail || "Service temporarily unavailable.";
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const post = <T,>(p: string, body?: unknown) =>
  api<T>(p, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

/* ---------------------------------------------------------------- auth */
export const AuthAPI = {
  register: (b: { email: string; username: string; password: string; full_name?: string }) =>
    post<AuthResponse>("/api/auth/register", b),

  // OAuth2 password flow: form-encoded, field is `username` but accepts email too.
  login: (identifier: string, password: string) =>
    api<AuthResponse>("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ username: identifier, password }),
    }),

  me: () => api<User>("/api/users/me"),
};

/* ------------------------------------------------------------- account */
export const AccountAPI = {
  balance: () => api<Balance>("/api/account/balance"),
  deposit: (amount: string) => post<Balance>("/api/account/deposit", { amount }),
  transactions: (limit = 50, offset = 0) =>
    api<Transaction[]>(`/api/account/transactions?limit=${limit}&offset=${offset}`),
};

/* -------------------------------------------------------------- market */
export const MarketAPI = {
  symbols: () => api<SymbolQuote[]>("/api/market/symbols"),
  quote: (s: string) => api<SymbolQuote>(`/api/market/quote/${s}`),
  candles: (s: string, interval: "1m" | "5m" = "1m", limit = 300) =>
    api<Candle[]>(`/api/market/candles/${s}?interval=${interval}&limit=${limit}`),
  trades: (s: string, limit = 30) => api<MarketTrade[]>(`/api/market/trades/${s}?limit=${limit}`),
  book: (s: string, depth = 10) => api<OrderBookSnapshot>(`/api/book/${s}?depth=${depth}`),
};

/* -------------------------------------------------------------- orders */
export const OrderAPI = {
  place: (b: {
    symbol: string; side: "BUY" | "SELL"; order_type: "LIMIT" | "MARKET";
    price?: string; quantity: number; client_order_id?: string;
  }) => post<Order>("/api/orders", b),
  list: (q: { status?: string; symbol?: string; limit?: number; offset?: number } = {}) => {
    const p = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v !== undefined && p.set(k, String(v)));
    return api<Order[]>(`/api/orders?${p.toString()}`);
  },
  get: (id: string) => api<Order>(`/api/orders/${id}`),
  cancel: (id: string) => api<{ status: string }>(`/api/orders/${id}`, { method: "DELETE" }),
};

/* ----------------------------------------------------------- portfolio */
export const PortfolioAPI = {
  get: () => api<Portfolio>("/api/portfolio"),
  pnl: () => api<Portfolio["totals"]>("/api/portfolio/pnl"),
};

/* -------------------------------------------------------- notifications */
export const NotificationAPI = {
  list: (limit = 20, unreadOnly = false) =>
    api<Notification[]>(`/api/notifications?limit=${limit}&unread_only=${unreadOnly}`),
  unreadCount: () => api<{ count: number }>("/api/notifications/unread-count"),
  markRead: (id: string) => post<void>(`/api/notifications/${id}/read`),
  markAllRead: () => post<{ marked: number }>("/api/notifications/read-all"),
};
