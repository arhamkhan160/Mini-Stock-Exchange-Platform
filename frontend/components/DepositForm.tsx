"use client";

import { useState } from "react";
import { AccountAPI, ApiError } from "@/lib/api";
import { validateAmount } from "@/lib/format";
import { Button, Card, ErrorBox, Input } from "./ui";
import { useToast } from "./Toast";

export default function DepositForm({ onDeposit }: { onDeposit: () => void }) {
  const [amount, setAmount] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { push } = useToast();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const validation = validateAmount(amount);
    if (validation) {
      setError(validation);
      return;
    }
    setError(null);
    setLoading(true);
    try {
      await AccountAPI.deposit(amount);
      push("success", `Deposited $${amount}`);
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
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <ErrorBox message={error} />}
        <Input
          label="Amount (USD)"
          required
          inputMode="decimal"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="100.00"
        />
        <Button type="submit" loading={loading} disabled={!amount} className="w-full">
          Deposit
        </Button>
      </form>
    </Card>
  );
}
