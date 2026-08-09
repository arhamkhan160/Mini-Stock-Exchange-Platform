"use client";

// OWNER: Team A. This is a working baseline — extend it, don't replace the file
// path (Navbar imports it).

import { useEffect, useRef, useState } from "react";
import { NotificationAPI } from "@/lib/api";
import type { Notification } from "@/lib/types";
import { timeAgo } from "@/lib/format";

export default function NotificationBell() {
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    const poll = () =>
      NotificationAPI.unreadCount()
        .then((r) => alive && setCount(r.count))
        .catch(() => {});
    poll();
    const t = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next) {
      try {
        setItems(await NotificationAPI.list(10));
        await NotificationAPI.markAllRead();
        setCount(0);
      } catch {
        /* notifications are non-critical — never break the navbar */
      }
    }
  };

  return (
    <div className="relative" ref={box}>
      <button
        onClick={toggle}
        aria-label={`Notifications${count ? `, ${count} unread` : ""}`}
        className="relative rounded-md border border-line bg-panel2 px-2.5 py-1.5 text-sm text-ink hover:bg-line"
      >
        🔔
        {count > 0 && (
          <span className="absolute -right-1 -top-1 min-w-[18px] rounded-full bg-down px-1 text-[10px] font-bold leading-[18px] text-white">
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-80 overflow-hidden rounded-lg border border-line bg-panel shadow-xl">
          <div className="border-b border-line px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted">
            Notifications
          </div>
          {items.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-muted">Nothing yet</p>
          ) : (
            <ul className="max-h-80 overflow-y-auto">
              {items.map((n) => (
                <li key={n.id} className="border-b border-line/60 px-3 py-2 last:border-0">
                  <p className="text-sm text-ink">{n.title}</p>
                  <p className="text-xs text-muted">{n.message}</p>
                  <p className="mt-0.5 text-[11px] text-muted">{timeAgo(n.created_at)}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
