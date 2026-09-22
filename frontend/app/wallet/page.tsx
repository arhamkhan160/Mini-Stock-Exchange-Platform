"use client";

import { useCallback, useEffect, useState } from "react";
import Protected from "@/components/Protected";
import DepositForm from "@/components/DepositForm";
import { AccountAPI, ApiError } from "@/lib/api";
import { money, timeAgo } from "@/lib/format";
import { Card, Empty, ErrorBox, PageHeader, Row, Spinner, Table, Tone } from "@/components/ui";
import type { Balance, Transaction } from "@/lib/types";

const TX_BADGE: Record<Transaction["type"], string> = {
  DEPOSIT: "bg-up/15 text-up",
  HOLD: "bg-warn/15 text-warn",
  RELEASE: "bg-accent/15 text-accent",
  TRADE_BUY: "bg-down/15 text-down",
  TRADE_SELL: "bg-up/15 text-up",
};

function WalletContent() {
  const [balance, setBalance] = useState<Balance | null>(null);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [bal, txs] = await Promise.all([AccountAPI.balance(), AccountAPI.transactions(50, 0)]);
      setBalance(bal);
      setTransactions(txs);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load wallet data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-6">
      <PageHeader title="Wallet" subtitle="Your virtual cash, reservations and ledger." />

      {error && <ErrorBox message={error} />}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <div className="space-y-6 md:col-span-1">
          <Card title="Available balance">
            <div className="num mb-4 text-2xl font-semibold text-ink">
              {balance ? money(balance.available_balance) : "—"}
            </div>
            <div className="space-y-2 border-t border-line pt-4 text-sm">
              <div className="flex justify-between">
                <span className="text-muted">Total cash</span>
                <span className="num text-ink">{balance ? money(balance.cash_balance) : "—"}</span>
              </div>
              <div className="flex justify-between">
                <span
                  className="cursor-help text-muted underline decoration-dotted"
                  title="Cash reserved against your open BUY orders. It's still yours, but it isn't available to place a new order until the order fills, is cancelled, or is rejected."
                >
                  Held for orders
                </span>
                <span className="num text-warn">{balance ? money(balance.held_balance) : "—"}</span>
              </div>
            </div>
          </Card>

          <DepositForm onDeposit={load} />
        </div>

        <div className="md:col-span-2">
          <Card title="Transaction history" padded={loading || transactions.length === 0}>
            {loading ? (
              <Spinner label="Loading transactions…" />
            ) : transactions.length === 0 ? (
              <Empty title="No transactions yet" hint="Deposit funds to get started." />
            ) : (
              <Table
                head={["Date", "Type", "Amount", "Balance after", "Details"]}
                align={["left", "left", "right", "right", "left"]}
              >
                {transactions.map((tx) => (
                  <Row key={tx.id}>
                    <td className="whitespace-nowrap px-3 py-2.5 text-xs text-muted">{timeAgo(tx.created_at)}</td>
                    <td className="px-3 py-2.5">
                      <span className={`inline-block whitespace-nowrap rounded px-2 py-0.5 text-2xs font-semibold ${TX_BADGE[tx.type] ?? "bg-panel2 text-muted"}`}>
                        {tx.type}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      <Tone value={tx.amount}>{money(tx.amount)}</Tone>
                    </td>
                    <td className="num px-3 py-2.5 text-right text-ink">{money(tx.balance_after)}</td>
                    <td className="px-3 py-2.5 text-xs text-muted">{tx.description || tx.reference_id || "—"}</td>
                  </Row>
                ))}
              </Table>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

export default function WalletPage() {
  return (
    <Protected>
      <WalletContent />
    </Protected>
  );
}
