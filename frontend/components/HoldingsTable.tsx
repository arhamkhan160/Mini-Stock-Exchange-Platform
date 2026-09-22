'use client';
import { Holding } from '@/lib/types';
import { Table, Row, Tone } from './ui';
import { money, price as fmtPrice, qty } from '@/lib/format';
import Link from 'next/link';

const HEAD = ['Symbol', 'Qty', 'Available', 'Avg cost', 'Last price', 'Market value', 'Unrealized P&L', 'Realized P&L'];
const ALIGN = ['left', 'right', 'right', 'right', 'right', 'right', 'right', 'right'] as const;

export default function HoldingsTable({ holdings }: { holdings: Holding[] }) {
  if (holdings.length === 0) return null;

  return (
    <Table head={HEAD} align={[...ALIGN]}>
      {holdings.map((h) => (
        <Row key={h.symbol}>
          <td className="px-3 py-2.5">
            <Link
              href={`/market/${h.symbol}`}
              className="font-semibold text-accent transition hover:underline"
            >
              {h.symbol}
            </Link>
          </td>
          <td className="num px-3 py-2.5 text-right text-ink">{qty(h.quantity)}</td>
          <td className="num px-3 py-2.5 text-right text-muted">{qty(h.available_quantity)}</td>
          <td className="num px-3 py-2.5 text-right text-ink">{money(h.avg_cost)}</td>
          <td className="num px-3 py-2.5 text-right text-ink">
            <span className="inline-flex items-center justify-end gap-1.5">
              {money(h.last_price)}
              {h.price_stale && (
                <span className="text-warn" title="Prices may be delayed" aria-label="Price may be delayed">
                  ⚠
                </span>
              )}
            </span>
          </td>
          <td className="num px-3 py-2.5 text-right text-ink">{money(h.market_value)}</td>
          <td className="px-3 py-2.5 text-right">
            <Tone value={h.unrealized_pnl} format="money" prefix="$" />
          </td>
          <td className="px-3 py-2.5 text-right">
            <Tone value={h.realized_pnl} format="money" prefix="$" />
          </td>
        </Row>
      ))}
    </Table>
  );
}
