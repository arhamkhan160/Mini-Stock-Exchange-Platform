'use client';
import { useEffect, useState } from 'react';
import { MarketAPI } from '@/lib/api';
import { useLivePrices } from '@/lib/ws';
import { SymbolQuote } from '@/lib/types';
import { money } from '@/lib/format';
import { Spinner, ErrorBox, Tone, Badge, Segmented, Empty } from '@/components/ui';
import dynamic from 'next/dynamic';
import OrderTicket from '@/components/OrderTicket';
import OrderBook from '@/components/OrderBook';
import RecentTrades from '@/components/RecentTrades';
import Protected from '@/components/Protected';

// Import CandleChart dynamically to prevent SSR hydration errors with lightweight-charts
const CandleChart = dynamic(() => import('@/components/CandleChart'), { ssr: false });

export default function MarketPage({ params }: { params: { symbol: string } }) {
  const [symbol, setSymbol] = useState<string>('');
  const [quote, setQuote] = useState<SymbolQuote | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [interval, setInterval] = useState<"1m" | "5m">("1m");

  useEffect(() => {
    // 404 gracefully if not valid
    const s = params.symbol.toUpperCase();
    setSymbol(s);

    MarketAPI.symbols()
      .then(symbols => {
        const found = symbols.find(x => x.symbol === s);
        if (!found) {
          setError("unknown symbol");
          return;
        }
        setQuote(found);
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [params.symbol]);

  const { prices, status } = useLivePrices([symbol]);
  const currentPrice = prices[symbol] || quote?.last_price || "0.0000";

  if (loading) return <Spinner label="Loading market…" />;
  if (error === "unknown symbol") {
    return <Empty title={`Unknown symbol: ${symbol}`} subtitle="Pick a symbol from the dashboard." />;
  }
  if (error) return <ErrorBox message={error} />;

  const handleRefresh = () => {
    // onPlaced callback for OrderTicket to refresh anything if needed
    // In our case, the portfolio updates and we rely on websockets,
    // but we can trigger a re-render if necessary.
  };

  return (
    <Protected>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-line pb-5">
          <div className="min-w-0">
            <h1 className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-2xl font-semibold text-ink">{symbol}</span>
              <span className="text-sm font-normal text-muted">{quote?.name}</span>
            </h1>
            <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="num text-xl font-semibold text-ink flash-up" key={currentPrice}>
                {money(currentPrice)}
              </span>
              <Tone value={quote?.change ?? 0} format="money" prefix="$" />
              <Tone value={quote?.change_pct ?? 0} format="pct" />
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Segmented
              label="Candle interval"
              value={interval}
              onChange={setInterval}
              options={[
                { value: "1m", label: "1m" },
                { value: "5m", label: "5m" },
              ]}
            />
            <Badge variant={status === "live" ? "success" : "warning"}>
              {status === "live" ? "Live" : "Reconnecting…"}
            </Badge>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          {/* Main Column - Chart and lower sections */}
          <div className="space-y-6 lg:col-span-2">
            <CandleChart symbol={symbol} interval={interval} />

            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
              <OrderBook symbol={symbol} />
              <RecentTrades symbol={symbol} />
            </div>
          </div>

          {/* Right Column - Order Ticket */}
          <div className="lg:sticky lg:top-20 lg:self-start">
            <OrderTicket symbol={symbol} lastPrice={currentPrice} onPlaced={handleRefresh} />
          </div>
        </div>
      </div>
    </Protected>
  );
}
