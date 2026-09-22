"use client";

import { useCallback, useEffect, useState } from "react";
import Protected from "@/components/Protected";
import SymbolTable from "@/components/SymbolTable";
import { AccountAPI, PortfolioAPI } from "@/lib/api";
import { PageHeader, Stat } from "@/components/ui";
import type { Balance, Portfolio } from "@/lib/types";

function DashboardContent() {
  const [balance, setBalance] = useState<Balance | null>(null);
  const [pnl, setPnl] = useState<Portfolio["totals"] | null>(null);
  // Portfolio is a separate service (CQRS) — if it's unreachable or still
  // catching up, the summary must degrade gracefully, not show a wrong number.
  const [pnlError, setPnlError] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(() => {
    AccountAPI.balance()
      .then(setBalance)
      .catch(() => {})
      .finally(() => setLoaded(true));
    PortfolioAPI.pnl()
      .then((p) => {
        setPnl(p);
        setPnlError(false);
      })
      .catch(() => setPnlError(true));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const lagging = pnlError ? "Catching up…" : undefined;

  return (
    <div className="space-y-6">
      <PageHeader title="Dashboard" subtitle="Your cash, positions and the live market." />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Stat
          label="Available cash"
          value={balance?.available_balance}
          loading={!loaded && !balance}
        />
        <Stat
          label="Portfolio value"
          value={pnl?.total_market_value}
          hint={lagging}
          loading={!loaded && !pnl && !pnlError}
        />
        <Stat
          label="Total P&L"
          value={pnl?.total_unrealized_pnl}
          tone
          hint={lagging}
          loading={!loaded && !pnl && !pnlError}
        />
      </div>

      <SymbolTable />
    </div>
  );
}

export default function DashboardPage() {
  return (
    <Protected>
      <DashboardContent />
    </Protected>
  );
}
