"use client";

// OWNER: TEAM C (Abidur Rahman Asif). Path and props are fixed by the contract —
// Team D's /market/[symbol] page imports it exactly as declared below.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AccountAPI, ApiError, OrderAPI, PortfolioAPI } from "@/lib/api";
import { money, num, price as fmtPrice, validatePrice, validateQuantity } from "@/lib/format";
import type { Holding, OrderType, Side } from "@/lib/types";
import { useToast } from "./Toast";
import { Button, Card, ErrorBox, Input } from "./ui";

/** Broadcast by OrderBook when a price level is clicked. Lets the book prefill
 *  the ticket without adding a prop the market page would have to wire up. */
export const PRICE_PICK_EVENT = "mse:price-pick";

export interface OrderTicketProps {
  symbol: string;
  lastPrice: string | null;
  /** Called after an order is accepted, so the parent can refresh lists. */
  onPlaced?: () => void;
}

export default function OrderTicket({ symbol, lastPrice, onPlaced }: OrderTicketProps) {
  const { push } = useToast();

  const [side, setSide] = useState<Side>("BUY");
  const [orderType, setOrderType] = useState<OrderType>("LIMIT");
  const [priceRaw, setPriceRaw] = useState("");
  const [quantityRaw, setQuantityRaw] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [cash, setCash] = useState<string | null>(null);
  const [holding, setHolding] = useState<Holding | null>(null);

  // One id per submission attempt, reused while that attempt is in flight — a
  // double-clicked Buy button hits the same idempotency key and buys once.
  const clientOrderId = useRef<string>(crypto.randomUUID());

  // Prefill from the last traded price, and follow it until the user types.
  const touched = useRef(false);
  useEffect(() => {
    if (!touched.current && lastPrice) setPriceRaw(fmtPrice(lastPrice));
  }, [lastPrice]);

  // Clicking a level in the order book fills the price in.
  useEffect(() => {
    const onPick = (e: Event) => {
      const detail = (e as CustomEvent<{ symbol: string; price: string }>).detail;
      if (detail?.symbol !== symbol) return;
      touched.current = true;
      setOrderType("LIMIT");
      setPriceRaw(fmtPrice(detail.price));
    };
    window.addEventListener(PRICE_PICK_EVENT, onPick);
    return () => window.removeEventListener(PRICE_PICK_EVENT, onPick);
  }, [symbol]);

  const loadBalances = useCallback(async () => {
    try {
      const balance = await AccountAPI.balance();
      setCash(balance.available_balance);
    } catch {
      setCash(null); // not fatal: the ticket still works, the hint just hides
    }
    try {
      const portfolio = await PortfolioAPI.get();
      setHolding(portfolio.holdings.find((h) => h.symbol === symbol) ?? null);
    } catch {
      setHolding(null);
    }
  }, [symbol]);

  useEffect(() => {
    loadBalances();
  }, [loadBalances]);

  const estimated = useMemo(() => {
    const reference = orderType === "LIMIT" ? priceRaw : lastPrice ?? "";
    const n = num(reference) * num(quantityRaw);
    return n > 0 ? n : null;
  }, [orderType, priceRaw, quantityRaw, lastPrice]);

  const submit = async () => {
    setError(null);

    // Mirrors the backend rules exactly, so the common mistakes never make a
    // round trip. The backend still validates — this is convenience, not trust.
    const qtyError = validateQuantity(quantityRaw);
    if (qtyError) return setError(qtyError);
    if (orderType === "LIMIT") {
      const priceError = validatePrice(priceRaw);
      if (priceError) return setError(priceError);
    }

    setSubmitting(true);
    try {
      const order = await OrderAPI.place({
        symbol,
        side,
        order_type: orderType,
        ...(orderType === "LIMIT" ? { price: priceRaw.trim() } : {}),
        quantity: Number(quantityRaw),
        client_order_id: clientOrderId.current,
      });
      push("success", `${side} ${order.quantity} ${symbol} submitted`);
      clientOrderId.current = crypto.randomUUID();   // next order is a new order
      setQuantityRaw("");
      onPlaced?.();
      loadBalances();
    } catch (e) {
      // Show the backend's own words: "insufficient buying power" has to be
      // readable, not flattened into "Request failed".
      setError(e instanceof ApiError ? e.message : "Could not place the order");
    } finally {
      setSubmitting(false);
    }
  };

  const shares = holding
    ? `${holding.available_quantity} of ${holding.quantity}` +
      (holding.reserved_quantity > 0 ? ` — ${holding.reserved_quantity} reserved by open orders` : "")
    : "0";

  return (
    <Card title={`Trade ${symbol}`}>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <Button
            variant={side === "BUY" ? "buy" : "ghost"}
            onClick={() => setSide("BUY")}
            aria-pressed={side === "BUY"}
          >
            Buy
          </Button>
          <Button
            variant={side === "SELL" ? "sell" : "ghost"}
            onClick={() => setSide("SELL")}
            aria-pressed={side === "SELL"}
          >
            Sell
          </Button>
        </div>

        <div className="grid grid-cols-2 gap-2">
          {(["LIMIT", "MARKET"] as OrderType[]).map((t) => (
            <button
              key={t}
              onClick={() => setOrderType(t)}
              className={`rounded-md border px-3 py-1.5 text-xs font-medium transition ${
                orderType === t ? "border-accent bg-accent/10 text-accent" : "border-line text-muted hover:text-ink"
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        <Input
          label="Quantity (shares)"
          inputMode="numeric"
          placeholder="0"
          value={quantityRaw}
          onChange={(e) => setQuantityRaw(e.target.value)}
          hint={side === "SELL" ? `Available: ${shares}` : undefined}
        />

        {orderType === "LIMIT" ? (
          <Input
            label="Limit price"
            inputMode="decimal"
            placeholder="0.00"
            value={priceRaw}
            onChange={(e) => {
              touched.current = true;
              setPriceRaw(e.target.value);
            }}
            hint="Must be a multiple of 0.01"
          />
        ) : (
          <p className="rounded-md border border-line bg-panel2 px-3 py-2 text-xs text-muted">
            Market orders fill immediately at the best available price. Anything that cannot fill
            straight away is cancelled — they are immediate-or-cancel.
          </p>
        )}

        <div className="flex items-center justify-between rounded-md bg-panel2 px-3 py-2 text-xs">
          <span className="text-muted">{orderType === "LIMIT" ? "Estimated cost" : "Estimated cost (at last)"}</span>
          <span className="num text-ink">{estimated === null ? "—" : money(estimated)}</span>
        </div>

        {side === "BUY" && cash !== null && (
          <div className="flex items-center justify-between px-3 text-xs">
            <span className="text-muted">Available cash</span>
            <span className="num text-ink">{money(cash)}</span>
          </div>
        )}

        {error && <ErrorBox message={error} />}

        <Button
          variant={side === "BUY" ? "buy" : "sell"}
          className="w-full"
          loading={submitting}
          onClick={submit}
        >
          {side === "BUY" ? "Buy" : "Sell"} {symbol}
        </Button>
      </div>
    </Card>
  );
}
