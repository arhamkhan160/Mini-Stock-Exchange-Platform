'use client';
import { createChart, ColorType, IChartApi, ISeriesApi } from 'lightweight-charts';
import { useEffect, useRef, useState } from 'react';
import { MarketAPI } from '@/lib/api';
import { subscribeMarket } from '@/lib/ws';
import { Spinner, Empty } from './ui';
import { Candle } from '@/lib/types';

export default function CandleChart({ symbol, interval }: { symbol: string; interval: "1m" | "5m" }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>(); 
  const series = useRef<ISeriesApi<'Candlestick'>>();
  
  const [loading, setLoading] = useState(true);
  const [empty, setEmpty] = useState(false);
  
  // Track the last candle so we can update it
  const lastCandle = useRef<Candle | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    
    chart.current = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: '#11151f' }, textColor: '#8b93a7' },
      grid: { vertLines: { color: '#1f2637' }, horzLines: { color: '#1f2637' } },
      timeScale: { timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    
    series.current = chart.current.addCandlestickSeries({     // v4 API
      upColor: '#26a69a', downColor: '#ef5350',
      wickUpColor: '#26a69a', wickDownColor: '#ef5350', borderVisible: false,
    });

    let active = true;

    async function loadData() {
      try {
        const data = await MarketAPI.candles(symbol, interval, 300);
        if (!active) return;
        
        if (data.length === 0) {
          setEmpty(true);
        } else {
          setEmpty(false);
          const uniqueData = Array.from(new Map(data.map(item => [item.time, item])).values());
          uniqueData.sort((a, b) => a.time - b.time);
          
          series.current?.setData(uniqueData as any);
          lastCandle.current = uniqueData[uniqueData.length - 1];
        }
      } catch (err) {
        console.error("Failed to load chart data", err);
      } finally {
        if (active) setLoading(false);
      }
    }
    
    loadData();

    return () => { 
      active = false;
      chart.current?.remove(); 
      chart.current = undefined; 
    };
  }, [symbol, interval]);

  useEffect(() => {
    if (empty || loading) return;
    
    const unsub = subscribeMarket([symbol], (tick) => {
      if (!series.current) return;
      
      const price = Number(tick.price);
      const minutes = interval === "1m" ? 1 : 5;
      const bucket = Math.floor(tick.ts / (minutes * 60)) * (minutes * 60);

      let current = lastCandle.current;
      
      if (current && current.time === bucket) {
        // Update current candle
        current = {
          ...current,
          high: Math.max(current.high, price),
          low: Math.min(current.low, price),
          close: price,
        };
      } else {
        // New candle
        const open = current ? current.close : price;
        current = {
          time: bucket,
          open,
          high: Math.max(open, price),
          low: Math.min(open, price),
          close: price,
          volume: 0
        };
      }
      
      lastCandle.current = current;
      series.current.update(current as any);
    });

    return unsub;
  }, [symbol, interval, empty, loading]);

  // The chart container must ALWAYS be mounted. Returning a spinner instead of
  // it left `ref.current` null, so the effect above bailed out on its first
  // line, never called loadData(), and never cleared `loading` — the chart sat
  // on "Loading…" forever. States overlay the container instead of replacing it.
  return (
    <div className="relative h-[420px] w-full overflow-hidden rounded-xl border border-[#1f2637]">
      <div ref={ref} className="absolute inset-0" />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-panel/80">
          <Spinner label="Loading chart…" />
        </div>
      )}
      {!loading && empty && (
        <div className="absolute inset-0 flex items-center justify-center bg-panel/80">
          <Empty
            title="No trading activity yet"
            subtitle="There is no historical data for this symbol."
          />
        </div>
      )}
    </div>
  );
}
