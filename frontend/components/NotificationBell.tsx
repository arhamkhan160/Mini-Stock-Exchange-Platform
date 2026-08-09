"use client";

// OWNER: Team A. Navbar imports this path — keep it.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { NotificationAPI } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/format";
import type { Notification } from "@/lib/types";

export const NOTIFICATION_TONE: Record<string, string> = {
  ORDER_FILLED: "text-up",
  ORDER_PARTIALLY_FILLED: "text-warn",
  ORDER_CANCELLED: "text-muted",
  ORDER_REJECTED: "text-down",
};

export const NOTIFICATION_ICON: Record<string, string> = {
  ORDER_FILLED: "✓",
  ORDER_PARTIALLY_FILLED: "◐",
  ORDER_CANCELLED: "✕",
  ORDER_REJECTED: "!",
};

const POLL_MS = 5000;

export default function NotificationBell() {
  const { user } = useAuth();
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // Poll the badge. Notifications are non-critical: a failure here must never
  // break the navbar, so every call swallows its error.
  useEffect(() => {
    if (!user) {
      setCount(0);
      return;
    }
    let alive = true;
    const poll = () =>
      NotificationAPI.unreadCount()
        .then((r) => alive && setCount(r.count))
        .catch(() => {});
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [user]);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, []);

  const toggle = useCallback(async () => {
    const next = !open;
    setOpen(next);
    if (!next) return;
    setLoading(true);
    try {
      setItems(await NotificationAPI.list(10));
    } catch {
      /* keep whatever was shown before */
    } finally {
      setLoading(false);
    }
  }, [open]);

  // Reading a single item is explicit — opening the panel must not silently
  // clear alerts the user has not looked at.
  const markOne = async (n: Notification) => {
    if (n.is_read) return;
    setItems((xs) => xs.map((x) => (x.id === n.id ? { ...x, is_read: true } : x)));
    setCount((c) => Math.max(0, c - 1));
    try {
      await NotificationAPI.markRead(n.id);
    } catch {
      /* optimistic; the next poll corrects the badge */
    }
  };

  const markAll = async () => {
    setItems((xs) => xs.map((x) => ({ ...x, is_read: true })));
    setCount(0);
    try {
      await NotificationAPI.markAllRead();
    } catch {
      /* next poll corrects it */
    }
  };

  if (!user) return null;

  return (
    <div className="relative" ref={box}>
      <button
        onClick={toggle}
        aria-label={count ? `Notifications, ${count} unread` : "Notifications"}
        aria-expanded={open}
        className="relative rounded-md border border-line bg-panel2 px-2.5 py-1.5 text-sm text-ink transition hover:bg-line"
      >
        <span aria-hidden>🔔</span>
        {count > 0 && (
          <span className="absolute -right-1.5 -top-1.5 min-w-[18px] rounded-full bg-down px-1 text-[10px] font-bold leading-[18px] text-white">
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 overflow-hidden rounded-lg border border-line bg-panel shadow-xl">
          <header className="flex items-center justify-between border-b border-line px-3 py-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted">Notifications</span>
            {count > 0 && (
              <button onClick={markAll} className="text-xs text-accent hover:underline">
                Mark all read
              </button>
            )}
          </header>

          {loading ? (
            <p className="px-3 py-6 text-center text-sm text-muted">Loading…</p>
          ) : items.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-muted">Nothing yet</p>
          ) : (
            <ul className="max-h-80 overflow-y-auto">
              {items.map((n) => (
                <li key={n.id}>
                  <button
                    onClick={() => markOne(n)}
                    className={`flex w-full gap-2 border-b border-line/60 px-3 py-2 text-left transition hover:bg-panel2 ${
                      n.is_read ? "opacity-60" : ""
                    }`}
                  >
                    <span className={`mt-0.5 text-sm ${NOTIFICATION_TONE[n.type] ?? "text-muted"}`} aria-hidden>
                      {NOTIFICATION_ICON[n.type] ?? "•"}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-ink">{n.title}</span>
                      <span className="block text-xs text-muted">{n.message}</span>
                      <span className="mt-0.5 block text-[11px] text-muted">{timeAgo(n.created_at)}</span>
                    </span>
                    {!n.is_read && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-accent" />}
                  </button>
                </li>
              ))}
            </ul>
          )}

          <footer className="border-t border-line px-3 py-2 text-center">
            <Link href="/notifications" onClick={() => setOpen(false)} className="text-xs text-accent hover:underline">
              View all notifications
            </Link>
          </footer>
        </div>
      )}
    </div>
  );
}
