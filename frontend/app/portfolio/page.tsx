'use client';
import { useEffect, useState, useCallback } from 'react';
import { PortfolioAPI, NotificationAPI } from '@/lib/api';
import { Portfolio } from '@/lib/types';
import { Spinner, ErrorBox, Card, Empty, PageHeader, Stat, Badge } from '@/components/ui';
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

  if (loading) return <Spinner label="Loading your portfolio…" />;
  if (error) return <ErrorBox message={error} />;
  if (!portfolio) return null;

  const { totals, holdings } = portfolio;
  const anyStale = holdings.some(h => h.price_stale);

  return (
    <Protected>
      <div className="space-y-6">
        <PageHeader
          title="Your portfolio"
          subtitle="Positions, cost basis and profit & loss."
          right={
            updating ? (
              <span className="flex items-center gap-2 text-xs text-muted">
                <Spinner inline /> Updating…
              </span>
            ) : undefined
          }
        />

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Total market value" value={totals.total_market_value} />
          <Stat label="Total cost basis" value={totals.total_cost_basis} />
          <Stat label="Unrealized P&L" value={totals.total_unrealized_pnl} tone />
          <Stat label="Realized P&L" value={totals.total_realized_pnl} tone />
        </div>

        <Card
          title="Holdings"
          subtitle={holdings.length > 0 ? `${holdings.length} position${holdings.length === 1 ? '' : 's'}` : undefined}
          right={anyStale ? <Badge variant="warning">Prices may be delayed</Badge> : undefined}
          padded={holdings.length === 0}
        >
          {holdings.length === 0 ? (
            <Empty
              title="You don't own anything yet"
              subtitle="Place your first order to start building your portfolio."
            />
          ) : (
            <HoldingsTable holdings={holdings} />
          )}
        </Card>
      </div>
    </Protected>
  );
}
