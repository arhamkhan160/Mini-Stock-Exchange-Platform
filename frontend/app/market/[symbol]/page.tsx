'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { MarketAPI } from '@/lib/api';
import { useLivePrices } from '@/lib/ws';
import { SymbolQuote } from '@/lib/types';
import { Spinner, ErrorBox, Tone, Badge, Button } from '@/components/ui';
import dynamic from 'next/dynamic';
import OrderTicket from '@/components/OrderTicket';
import OrderBook from '@/components/OrderBook';
import RecentTrades from '@/components/RecentTrades';
import Protected from '@/components/Protected';

// Import CandleChart dynamically to prevent SSR hydration errors with lightweight-charts
const CandleChart = dynamic(() => import('@/components/CandleChart'), { ssr: false });

export default function MarketPage({ params }: { params: { symbol: string } }) {
  const router = useRouter();
  const [symbol, setSymbol] = useState<string>('');
  const [quote, setQuote] = useState<SymbolQuote | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [interval, setInterval] = useState<"1m" | "5m">("1m");
  const [ticketPrice, setTicketPrice] = useState<string>("");

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

  if (loading) return <div className="p-8 text-center"><Spinner /></div>;
  if (error === "unknown symbol") return <div className="p-8 text-center text-red-400">Unknown symbol: {symbol}</div>;
  if (error) return <div className="p-8"><ErrorBox message={error} /></div>;

  const handleRefresh = () => {
    // onPlaced callback for OrderTicket to refresh anything if needed
    // In our case, the portfolio updates and we rely on websockets, 
    // but we can trigger a re-render if necessary.
  };

  return (
    <Protected>
      <div className="max-w-7xl mx-auto p-4 space-y-4">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between border-b border-[#1f2637] pb-4">
          <div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-3">
              {symbol}
              <span className="text-lg font-normal text-gray-400">{quote?.name}</span>
            </h1>
            <div className="flex items-center gap-3 mt-1">
              <span className="text-xl num flash-up" key={currentPrice}>${currentPrice}</span>
              <Tone value={Number(quote?.change_pct || 0)} format="pct" />
              <Tone value={Number(quote?.change || 0)} format="money" prefix="$" />
            </div>
          </div>
          
          <div className="flex items-center gap-4">
            <div className="flex rounded-md overflow-hidden bg-[#1f2637]">
              <button 
                onClick={() => setInterval("1m")} 
                className={`px-3 py-1 text-sm ${interval === "1m" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"}`}
              >
                1m
              </button>
              <button 
                onClick={() => setInterval("5m")} 
                className={`px-3 py-1 text-sm ${interval === "5m" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"}`}
              >
                5m
              </button>
            </div>
            
            <Badge variant={status === "live" ? "success" : "warning"}>
              {status === "live" ? "Live" : "Reconnecting..."}
            </Badge>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Main Column - Chart and lower sections */}
          <div className="lg:col-span-2 space-y-6">
            <CandleChart symbol={symbol} interval={interval} />
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <OrderBook symbol={symbol} onPriceClick={(p) => setTicketPrice(p)} />
              <RecentTrades symbol={symbol} />
            </div>
          </div>

          {/* Right Column - Order Ticket */}
          <div>
            <OrderTicket symbol={symbol} lastPrice={currentPrice} onPlaced={handleRefresh} />
          </div>
        </div>
      </div>
    </Protected>
  );
}
