'use client';
import { Holding } from '@/lib/types';
import { Table, Tone, Badge } from './ui';
import Link from 'next/link';

export default function HoldingsTable({ holdings }: { holdings: Holding[] }) {
  if (holdings.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <Table>
        <thead>
          <tr>
            <th className="text-left">Symbol</th>
            <th className="text-right">Qty</th>
            <th className="text-right">Available</th>
            <th className="text-right">Avg Cost</th>
            <th className="text-right">Last Price</th>
            <th className="text-right">Market Value</th>
            <th className="text-right">Unrealized P&L</th>
            <th className="text-right">Realized P&L</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((h) => {
            const unrealized = Number(h.unrealized_pnl);
            const realized = Number(h.realized_pnl);
            
            return (
              <tr key={h.symbol}>
                <td className="font-semibold text-white">
                  <Link href={`/market/${h.symbol}`} className="hover:underline text-blue-400">
                    {h.symbol}
                  </Link>
                </td>
                <td className="text-right num">{h.quantity}</td>
                <td className="text-right num text-gray-400">{h.available_quantity}</td>
                <td className="text-right num">${h.avg_cost}</td>
                <td className="text-right num">
                  ${h.last_price}
                  {h.price_stale && (
                    <span className="ml-2 text-xs text-yellow-500" title="Prices may be delayed">
                      ⚠️
                    </span>
                  )}
                </td>
                <td className="text-right num">${h.market_value}</td>
                <td className="text-right num">
                  <Tone value={unrealized} format="money" prefix="$" />
                </td>
                <td className="text-right num">
                  <Tone value={realized} format="money" prefix="$" />
                </td>
              </tr>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}
