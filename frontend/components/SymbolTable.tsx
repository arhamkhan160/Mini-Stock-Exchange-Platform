"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { MarketAPI, ApiError } from "@/lib/api";
import { useLivePrices } from "@/lib/ws";
import { money, pct, price as fmtPrice, toneOf } from "@/lib/format";
import { Button, Card, Empty, ErrorBox, LiveDot, Row, Spinner, Table } from "./ui";
import type { SymbolQuote } from "@/lib/types";

function useFlash(value: string | undefined): "up" | "down" | null {
  const prev = useRef<string | undefined>(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);

  useEffect(() => {
    if (value !== undefined && prev.current !== undefined && value !== prev.current) {
      setFlash(Number(value) >= Number(prev.current) ? "up" : "down");
      const t = setTimeout(() => setFlash(null), 600);
      prev.current = value;
      return () => clearTimeout(t);
    }
    prev.current = value;
  }, [value]);

  return flash;
}

function SymbolRow({ quote, livePrice }: { quote: SymbolQuote; livePrice: string | undefined }) {
  const last = livePrice ?? quote.last_price;
  const flash = useFlash(livePrice);

  return (
    <Row>
      <td className="px-3 py-2.5">
        <div className="font-semibold text-ink">{quote.symbol}</div>
        <div className="text-xs text-muted">{quote.name}</div>
      </td>
      <td className={`num px-3 py-2.5 text-right text-ink ${flash ? `flash-${flash}` : ""}`}>
        {money(last)}
      </td>
      <td className={`num px-3 py-2.5 text-right ${toneOf(quote.change_pct)}`}>
        {fmtPrice(quote.change)} ({pct(quote.change_pct)})
      </td>
      <td className="px-3 py-2.5 text-right">
        <Link href={`/market/${quote.symbol}`}>
          <Button variant="ghost" size="sm">Trade</Button>
        </Link>
      </td>
    </Row>
  );
}

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
      right={<LiveDot status={status} />}
      padded={false}
    >
      <Table head={["Symbol", "Last price", "24h change", ""]} align={["left", "right", "right", "right"]}>
        {symbols.map((s) => (
          <SymbolRow key={s.symbol} quote={s} livePrice={prices[s.symbol]} />
        ))}
      </Table>
    </Card>
  );
}
