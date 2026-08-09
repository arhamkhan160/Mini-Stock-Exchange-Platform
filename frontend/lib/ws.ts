"use client";

import { useEffect, useRef, useState } from "react";
import { WS_BASE } from "./api";
import type { Tick } from "./types";

export type WsStatus = "connecting" | "live" | "reconnecting";

/**
 * Live market ticks with exponential-backoff reconnect.
 *
 * ALWAYS use the returned cleanup — React 18 StrictMode mounts effects twice in
 * dev, and without cleanup you get two sockets and doubled ticks.
 */
export function subscribeMarket(
  symbols: string[],
  onTick: (t: Tick) => void,
  onStatus?: (s: WsStatus) => void,
): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const url = `${WS_BASE}/ws/market${symbols.length ? `?symbols=${symbols.join(",")}` : ""}`;

  const open = () => {
    onStatus?.(attempt === 0 ? "connecting" : "reconnecting");
    try {
      ws = new WebSocket(url);
    } catch {
      schedule();
      return;
    }
    ws.onopen = () => {
      attempt = 0;
      onStatus?.("live");
    };
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg?.type === "tick") onTick(msg as Tick);
      } catch {
        /* ignore malformed frame */
      }
    };
    ws.onerror = () => ws?.close();
    ws.onclose = () => {
      if (closed) return;
      onStatus?.("reconnecting");
      schedule();
    };
  };

  const schedule = () => {
    const delay = Math.min(1000 * 2 ** attempt++, 15000);
    timer = setTimeout(open, delay);
  };

  open();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    ws?.close();
  };
}

/** Convenience hook: latest price per symbol, kept live. */
export function useLivePrices(symbols: string[]) {
  const [prices, setPrices] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<WsStatus>("connecting");
  const key = symbols.join(",");
  const ref = useRef(symbols);
  ref.current = symbols;

  useEffect(() => {
    if (!key) return;
    const stop = subscribeMarket(
      ref.current,
      (t) => setPrices((p) => (p[t.symbol] === t.price ? p : { ...p, [t.symbol]: t.price })),
      setStatus,
    );
    return stop; // <- the cleanup that StrictMode needs
  }, [key]);

  return { prices, status };
}
