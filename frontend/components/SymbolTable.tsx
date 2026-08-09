"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MarketAPI, ApiError } from "@/lib/api";
import { useLivePrices } from "@/lib/ws";
import { money, pct, price as fmtPrice, toneOf } from "@/lib/format";
import { Button, Card, Empty, ErrorBox, Spinner, Table } from "./ui";
import type { SymbolQuote } from "@/lib/types";

export default function SymbolTable() {
  const [symbols, setSymbols] = useState<SymbolQuote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    MarketAPI.symbols()
      .then(setSymbols)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load markets"))
      .finally(() => setLoading(false));
  }, []);

  const { prices, status } = useLivePrices(symbols.map((s) => s.symbol));

  if (loading) return <Spinner label="Loading markets…" />;
  if (error) return <ErrorBox message={error} />;
  if (symbols.length === 0) return <Empty title="No symbols available" />;

  return (
    <Card
      title="Live markets"
      right={
        <span className="text-xs text-muted">
          {status === "live" ? "● live" : status === "connecting" ? "connecting…" : "reconnecting…"}
        </span>
      }
    >
      <Table head={["Symbol", "Last price", "24h change", ""]}>
        {symbols.map((s) => {
          const last = prices[s.symbol] ?? s.last_price;
          return (
            <tr key={s.symbol} className="border-b border-line last:border-0">
              <td className="px-3 py-3">
                <div className="font-semibold text-ink">{s.symbol}</div>
                <div className="text-xs text-muted">{s.name}</div>
              </td>
              <td className="num px-3 py-3 text-ink">{money(last)}</td>
              <td className={`num px-3 py-3 ${toneOf(s.change_pct)}`}>
                {fmtPrice(s.change)} ({pct(s.change_pct)})
              </td>
              <td className="px-3 py-3 text-right">
                <Link href={`/market/${s.symbol}`}>
                  <Button variant="ghost">Trade</Button>
                </Link>
              </td>
            </tr>
          );
        })}
      </Table>
    </Card>
  );
}
