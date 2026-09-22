"use client";

// OWNER: TEAM C (Abidur Rahman Asif). Path and props are fixed by the contract.

import { useEffect, useState } from "react";
import { ApiError, MarketAPI } from "@/lib/api";
import { price as fmtPrice, qty } from "@/lib/format";
import type { BookLevel, OrderBookSnapshot } from "@/lib/types";
import { PRICE_PICK_EVENT } from "./OrderTicket";
import { Card, Empty, ErrorBox, Spinner } from "./ui";

const DEPTH = 10;
// The book is polled, not streamed: it has no WS feed, and 2s is well inside
// what a human reads as "live". Documented rather than hidden.
const POLL_MS = 2000;

export interface OrderBookProps {
  symbol: string;
  /** Clicking a price level should prefill the order ticket. */
  onPriceClick?: (price: string) => void;
}

/** Running totals, so the depth bar shows liquidity available UP TO this level. */
function cumulative(levels: BookLevel[]): number[] {
  let total = 0;
  return levels.map((l) => (total += l.quantity));
}

export default function OrderBook({ symbol, onPriceClick }: OrderBookProps) {
  const [book, setBook] = useState<OrderBookSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const snapshot = await MarketAPI.book(symbol, DEPTH);
        if (!alive) return;
        setBook(snapshot);
        setError(null);
      } catch (e) {
        if (alive) setError(e instanceof ApiError ? e.message : "Could not load the order book");
      }
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [symbol]);

  const pick = (p: string) => {
    onPriceClick?.(p);
    // Also announced globally so OrderTicket picks it up wherever it is
    // mounted, without the parent page having to wire the two together.
    window.dispatchEvent(new CustomEvent(PRICE_PICK_EVENT, { detail: { symbol, price: p } }));
  };

  const bids = book?.bids ?? [];
  // Asks come back ascending; render descending so the spread sits in the
  // middle with the best ask directly above the best bid.
  const asks = book?.asks ?? [];
  const askRows = [...asks].reverse();
  const maxDepth = Math.max(1, ...cumulative(bids), ...cumulative(asks));

  const row = (level: BookLevel, running: number, tone: "up" | "down") => (
    <button
      key={`${tone}-${level.price}`}
      onClick={() => pick(level.price)}
      title="Click to fill the order ticket with this price"
      className="relative grid w-full grid-cols-3 rounded px-2 py-1 text-right text-xs transition-colors hover:bg-panel2"
    >
      <span
        aria-hidden
        className={`absolute inset-y-0 right-0 rounded-sm ${tone === "up" ? "bg-up/15" : "bg-down/15"}`}
        style={{ width: `${(running / maxDepth) * 100}%` }}
      />
      <span className={`num relative text-left ${tone === "up" ? "text-up" : "text-down"}`}>
        {fmtPrice(level.price)}
      </span>
      <span className="num relative text-ink">{qty(level.quantity)}</span>
      <span className="num relative text-muted">{level.orders}</span>
    </button>
  );

  const askCumulative = cumulative(asks);
  const bidCumulative = cumulative(bids);

  return (
    <Card title="Order book" right={<span className="text-xs text-muted">{symbol}</span>}>
      {error && <ErrorBox message={error} />}

      {book === null && !error ? (
        <Spinner label="Loading the book…" />
      ) : book && bids.length === 0 && asks.length === 0 ? (
        <Empty title="The book is empty" hint="Place a limit order and it will rest here." />
      ) : (
        <div className="space-y-1">
          <div className="grid grid-cols-3 px-2 pb-1 text-right text-2xs uppercase tracking-wider text-faint">
            <span className="text-left">Price</span>
            <span>Size</span>
            <span>Orders</span>
          </div>

          <div className="flex flex-col">
            {askRows.map((level, i) => row(level, askCumulative[asks.length - 1 - i], "down"))}
          </div>

          <div className="flex items-center justify-between border-y border-line px-2 py-1.5 text-xs">
            <span className="text-muted">Spread</span>
            <span className="num text-ink">{book?.spread ? fmtPrice(book.spread) : "—"}</span>
          </div>

          <div className="flex flex-col">
            {bids.map((level, i) => row(level, bidCumulative[i], "up"))}
          </div>
        </div>
      )}
    </Card>
  );
}
