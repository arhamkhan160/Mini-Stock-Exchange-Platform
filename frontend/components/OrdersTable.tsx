"use client";

// OWNER: TEAM C (Abidur Rahman Asif).

import Link from "next/link";
import { clockTime, price as fmtPrice, qty } from "@/lib/format";
import type { Order } from "@/lib/types";
import { Badge, Button, Empty, Row, Table } from "./ui";

const HEAD = ["Time", "Symbol", "Side", "Type", "Price", "Filled", "Avg fill", "Status", ""];
const ALIGN = ["left", "left", "left", "left", "right", "right", "right", "left", "right"] as const;

export interface OrdersTableProps {
  orders: Order[];
  emptyTitle: string;
  emptyHint?: string;
  /** Omit for the history table — nothing there can be cancelled. */
  onCancel?: (order: Order) => void;
  cancelling?: Set<string>;
}

export default function OrdersTable({
  orders, emptyTitle, emptyHint, onCancel, cancelling,
}: OrdersTableProps) {
  if (orders.length === 0) return <Empty title={emptyTitle} hint={emptyHint} />;

  return (
    <Table head={HEAD} align={[...ALIGN]}>
      {orders.map((o) => (
        <Row key={o.id}>
          <td className="num px-3 py-2 text-muted">{clockTime(o.created_at)}</td>
          <td className="px-3 py-2">
            <Link href={`/market/${o.symbol}`} className="num text-accent hover:underline">
              {o.symbol}
            </Link>
          </td>
          <td className="px-3 py-2"><Badge value={o.side} /></td>
          <td className="px-3 py-2 text-muted">{o.order_type}</td>
          <td className="num px-3 py-2 text-right text-ink">{o.price ? fmtPrice(o.price) : "MKT"}</td>
          <td className="num px-3 py-2 text-right text-ink">
            {qty(o.filled_quantity)} / {qty(o.quantity)}
          </td>
          <td className="num px-3 py-2 text-right text-muted">
            {o.filled_quantity > 0 ? fmtPrice(o.avg_fill_price) : "—"}
          </td>
          <td className="px-3 py-2">
            {/* Badge already renders CANCEL_PENDING as "CANCELLING…", never
                "Cancelled" — the order can still come back FILLED. */}
            <span title={o.reject_reason ?? undefined}>
              <Badge value={o.status} />
            </span>
          </td>
          <td className="px-3 py-2 text-right">
            {onCancel && (
              <Button
                variant="danger"
                size="sm"
                disabled={o.status === "CANCEL_PENDING" || cancelling?.has(o.id)}
                onClick={() => onCancel(o)}
              >
                Cancel
              </Button>
            )}
          </td>
        </Row>
      ))}
    </Table>
  );
}
