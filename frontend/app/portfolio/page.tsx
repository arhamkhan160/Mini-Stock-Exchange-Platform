'use client';
import { useEffect, useState, useCallback } from 'react';
import { PortfolioAPI, NotificationAPI } from '@/lib/api';
import { Portfolio } from '@/lib/types';
import { Spinner, ErrorBox, Card, Tone, Empty } from '@/components/ui';
import { signed } from '@/lib/format';
import Protected from '@/components/Protected';
import HoldingsTable from '@/components/HoldingsTable';

export default function PortfolioPage() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchPortfolio = useCallback(async (isUpdate = false) => {
    if (isUpdate) setUpdating(true);
    try {
      const data = await PortfolioAPI.get();
      setPortfolio(data);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
      setUpdating(false);
    }
  }, []);

  useEffect(() => {
    fetchPortfolio();
  }, [fetchPortfolio]);

  useEffect(() => {
    // Poll for notifications to catch fills
    // CQRS lag: wait 1s after fill to fetch portfolio
    const interval = setInterval(async () => {
      try {
        const notifs = await NotificationAPI.list(5, true);
        const hasNewFills = notifs.some(n => n.type === 'ORDER_FILLED');
        if (hasNewFills) {
          setUpdating(true);
          setTimeout(() => fetchPortfolio(true), 1000);
        }
      } catch (e) {
        // ignore
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [fetchPortfolio]);

  if (loading) return <div className="p-8 text-center"><Spinner /></div>;
  if (error) return <div className="p-8"><ErrorBox message={error} /></div>;
  if (!portfolio) return null;

  const { totals, holdings } = portfolio;
  const anyStale = holdings.some(h => h.price_stale);

  return (
    <Protected>
      <div className="max-w-7xl mx-auto p-4 space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-white">Your Portfolio</h1>
          {updating && <span className="text-sm text-gray-400 flex items-center gap-2"><Spinner /> updating...</span>}
        </div>

        {/* Summary Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <Card className="p-4">
            <h3 className="text-sm text-gray-400 font-medium">Total Market Value</h3>
            <p className="text-2xl font-bold text-white mt-1 num">${totals.total_market_value}</p>
          </Card>
          
          <Card className="p-4">
            <h3 className="text-sm text-gray-400 font-medium">Total Cost Basis</h3>
            <p className="text-2xl font-bold text-white mt-1 num">${totals.total_cost_basis}</p>
          </Card>

          <Card className="p-4">
            <h3 className="text-sm text-gray-400 font-medium">Unrealized P&L</h3>
            <div className="text-2xl font-bold mt-1 num">
              <Tone value={Number(totals.total_unrealized_pnl)}>{signed(Number(totals.total_unrealized_pnl))}</Tone>
            </div>
          </Card>

          <Card className="p-4">
            <h3 className="text-sm text-gray-400 font-medium">Realized P&L</h3>
            <div className="text-2xl font-bold mt-1 num">
              <Tone value={Number(totals.total_realized_pnl)}>{signed(Number(totals.total_realized_pnl))}</Tone>
            </div>
          </Card>
        </div>

        {/* Holdings */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white">Holdings</h2>
            {anyStale && <span className="text-sm text-yellow-500">Prices may be delayed</span>}
          </div>
          
          {holdings.length === 0 ? (
            <Card className="p-8">
              <Empty 
                title="You don't own anything yet" 
                hint="Place your first order to start building your portfolio." 
              />
            </Card>
          ) : (
            <Card className="p-0 overflow-hidden">
              <HoldingsTable holdings={holdings} />
            </Card>
          )}
        </div>
      </div>
    </Protected>
  );
}
