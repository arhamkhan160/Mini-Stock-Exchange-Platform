"use client";

// OWNER: TEAM C (Abidur Rahman Asif). Path and props are fixed by the contract.

import { useEffect, useState } from "react";
import { ApiError, MarketAPI } from "@/lib/api";
import { clockTime, price as fmtPrice, qty } from "@/lib/format";
import type { MarketTrade } from "@/lib/types";
import { Card, Empty, ErrorBox } from "./ui";

const LIMIT = 30;
const POLL_MS = 3000;

export interface RecentTradesProps {
  symbol: string;
}

export default function RecentTrades({ symbol }: RecentTradesProps) {
  const [trades, setTrades] = useState<MarketTrade[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const rows = await MarketAPI.trades(symbol, LIMIT);
        if (!alive) return;
        setTrades(rows);
        setError(null);
      } catch (e) {
        if (alive) setError(e instanceof ApiError ? e.message : "Could not load recent trades");
      }
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [symbol]);

  return (
    <Card title="Recent trades" right={<span className="text-xs text-muted">{symbol}</span>}>
      {error && <ErrorBox message={error} />}

      {trades && trades.length === 0 ? (
        <Empty title="No trades yet" hint="Trades appear the moment two orders cross." />
      ) : (
        <div className="space-y-1">
          <div className="grid grid-cols-3 px-2 text-right text-[11px] uppercase tracking-wide text-muted">
            <span className="text-left">Price</span>
            <span>Size</span>
            <span>Time</span>
          </div>
          {(trades ?? []).map((t) => (
            <div key={t.trade_id} className="grid grid-cols-3 px-2 py-1 text-right text-xs">
              {/* Coloured by who crossed the spread, which is how a tape reads. */}
              <span className={`num text-left ${t.aggressor_side === "BUY" ? "text-up" : "text-down"}`}>
                {fmtPrice(t.price)}
              </span>
              <span className="num text-ink">{qty(t.quantity)}</span>
              <span className="num text-muted">{clockTime(t.executed_at)}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
