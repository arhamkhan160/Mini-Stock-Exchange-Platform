"use client";

// OWNER: TEAM C (Abidur Rahman Asif).

import { useCallback, useEffect, useRef, useState } from "react";
import OrdersTable from "@/components/OrdersTable";
import Protected from "@/components/Protected";
import { useToast } from "@/components/Toast";
import { Card, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { ApiError, OrderAPI } from "@/lib/api";
import type { Order, OrderStatus } from "@/lib/types";

const OPEN_STATUSES: OrderStatus[] = ["NEW", "PARTIALLY_FILLED", "CANCEL_PENDING"];
const NON_TERMINAL: OrderStatus[] = ["PENDING", ...OPEN_STATUSES];
const POLL_MS = 3000;

function OrdersInner() {
  const { push } = useToast();
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const rows = await OrderAPI.list({ limit: 200 });
      setOrders(rows);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load your orders");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Poll only while something can still change. A page full of FILLED orders
  // has no reason to hammer the gateway every three seconds.
  const anyLive = (orders ?? []).some((o) => NON_TERMINAL.includes(o.status));
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    if (!anyLive) return;
    const timer = setInterval(() => loadRef.current(), POLL_MS);
    return () => clearInterval(timer);
  }, [anyLive]);

  const cancel = async (order: Order) => {
    setCancelling((s) => new Set(s).add(order.id));
    // Optimistic: the server answers 202, not "cancelled" — the book decides.
    setOrders((xs) =>
      (xs ?? []).map((o) => (o.id === order.id ? { ...o, status: "CANCEL_PENDING" as OrderStatus } : o)),
    );
    try {
      await OrderAPI.cancel(order.id);
      push("info", `Cancellation requested for ${order.symbol}`);
    } catch (e) {
      push("error", e instanceof ApiError ? e.message : "Could not cancel the order");
    } finally {
      setCancelling((s) => {
        const next = new Set(s);
        next.delete(order.id);
        return next;
      });
      load();
    }
  };

  if (orders === null && !error) return <Spinner label="Loading your orders…" />;

  const open = (orders ?? []).filter((o) => NON_TERMINAL.includes(o.status));
  const history = (orders ?? []).filter((o) => !NON_TERMINAL.includes(o.status));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Orders"
        subtitle="Working orders update on their own. A cancellation shows as “Cancelling…” until the matching engine confirms it — it can still come back filled."
      />

      {error && <ErrorBox message={error} />}

      <Card title={`Open orders (${open.length})`} padded={open.length === 0}>
        <OrdersTable
          orders={open}
          emptyTitle="No working orders"
          emptyHint="Orders you place appear here until they fill or are cancelled."
          onCancel={cancel}
          cancelling={cancelling}
        />
      </Card>

      <Card title="History" padded={history.length === 0}>
        <OrdersTable
          orders={history}
          emptyTitle="No completed orders yet"
          emptyHint="Filled, cancelled and rejected orders are kept here."
        />
      </Card>
    </div>
  );
}

export default function OrdersPage() {
  return (
    <Protected>
      <OrdersInner />
    </Protected>
  );
}
