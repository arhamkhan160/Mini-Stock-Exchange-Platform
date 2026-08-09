"use client";

import { useState } from "react";
import { AccountAPI, ApiError } from "@/lib/api";
import { validateAmount } from "@/lib/format";
import { Button, Card, ErrorBox, Input } from "./ui";
import { useToast } from "./Toast";

const QUICK_AMOUNTS = [
  { label: "1k", value: "1000" },
  { label: "10k", value: "10000" },
  { label: "100k", value: "100000" },
];

export default function DepositForm({ onDeposit }: { onDeposit: () => void }) {
  const [amount, setAmount] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { push } = useToast();

  async function submitAmount(value: string) {
    const validation = validateAmount(value);
    if (validation) {
      setError(validation);
      return;
    }
    setError(null);
    setLoading(true);
    try {
      await AccountAPI.deposit(value);
      push("success", `Deposited $${value}`);
      setAmount("");
      onDeposit();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Deposit failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card title="Deposit funds">
      <form onSubmit={(e) => { e.preventDefault(); submitAmount(amount); }} className="space-y-4">
        {error && <ErrorBox message={error} />}
        <Input
          label="Amount (USD)"
          required
          inputMode="decimal"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="100.00"
        />
        <div className="flex gap-2">
          {QUICK_AMOUNTS.map((q) => (
            <Button
              key={q.value}
              type="button"
              variant="ghost"
              disabled={loading}
              className="flex-1"
              onClick={() => submitAmount(q.value)}
            >
              +{q.label}
            </Button>
          ))}
        </div>
        <Button type="submit" loading={loading} disabled={!amount} className="w-full">
          Deposit
        </Button>
      </form>
    </Card>
  );
}
