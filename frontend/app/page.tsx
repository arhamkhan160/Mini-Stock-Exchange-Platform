"use client";

import { useCallback, useEffect, useState } from "react";
import Protected from "@/components/Protected";
import SymbolTable from "@/components/SymbolTable";
import { AccountAPI, ApiError, PortfolioAPI } from "@/lib/api";
import { money } from "@/lib/format";
import { Card, Tone } from "@/components/ui";
import type { Balance, Portfolio } from "@/lib/types";

function DashboardContent() {
  const [balance, setBalance] = useState<Balance | null>(null);
  const [pnl, setPnl] = useState<Portfolio["totals"] | null>(null);
  // Portfolio is a separate service (CQRS) — if it's unreachable or still
  // catching up, the summary must degrade gracefully, not show a wrong number.
  const [pnlError, setPnlError] = useState(false);

  const load = useCallback(() => {
    AccountAPI.balance()
      .then(setBalance)
      .catch(() => {});
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

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-ink">Dashboard</h1>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card title="Available cash">
          <div className="num text-2xl font-bold text-ink">
            {balance ? money(balance.available_balance) : "—"}
          </div>
        </Card>
        <Card title="Portfolio value">
          <div className="num text-2xl font-bold text-ink">
            {pnl ? money(pnl.total_market_value) : pnlError ? "updating…" : "—"}
          </div>
        </Card>
        <Card title="Total P&L">
          {pnl ? (
            <Tone value={pnl.total_unrealized_pnl}>
              <span className="num text-2xl font-bold">{money(pnl.total_unrealized_pnl)}</span>
            </Tone>
          ) : (
            <div className="num text-2xl font-bold text-muted">{pnlError ? "updating…" : "—"}</div>
          )}
        </Card>
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
