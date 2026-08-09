"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import Protected from "@/components/Protected";
import { Button, Card, Empty, ErrorBox, Spinner } from "@/components/ui";
import { NOTIFICATION_ICON, NOTIFICATION_TONE } from "@/components/NotificationBell";
import { NotificationAPI, ApiError } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { Notification } from "@/lib/types";

const PAGE_SIZE = 25;

function NotificationsInner() {
  const [items, setItems] = useState<Notification[]>([]);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (onlyUnread: boolean) => {
      setLoading(true);
      setError(null);
      try {
        const rows = await NotificationAPI.list(PAGE_SIZE, onlyUnread);
        setItems(rows);
        setHasMore(rows.length === PAGE_SIZE);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Could not load notifications");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    load(unreadOnly);
  }, [load, unreadOnly]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const rows = await NotificationAPI.list(PAGE_SIZE, unreadOnly, items.length);
      // De-duplicate by id: new alerts arriving mid-scroll shift the offset
      // window and would otherwise repeat a row.
      const seen = new Set(items.map((i) => i.id));
      setItems([...items, ...rows.filter((r) => !seen.has(r.id))]);
      setHasMore(rows.length === PAGE_SIZE);
    } catch {
      setHasMore(false);
    } finally {
      setLoadingMore(false);
    }
  };

  const markOne = async (n: Notification) => {
    if (n.is_read) return;
    setItems((xs) => xs.map((x) => (x.id === n.id ? { ...x, is_read: true } : x)));
    try {
      await NotificationAPI.markRead(n.id);
    } catch {
      /* optimistic */
    }
  };

  const markAll = async () => {
    setItems((xs) => xs.map((x) => ({ ...x, is_read: true })));
    try {
      await NotificationAPI.markAllRead();
      if (unreadOnly) load(true);
    } catch {
      /* optimistic */
    }
  };

  const unread = items.filter((i) => !i.is_read).length;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-ink">Notifications</h1>
          <p className="text-sm text-muted">
            Fill, cancellation and rejection alerts, delivered asynchronously.
          </p>
        </div>
        <Button variant="ghost" onClick={markAll} disabled={unread === 0}>
          Mark all read
        </Button>
      </div>

      <Card
        title={unreadOnly ? "Unread" : "All notifications"}
        right={
          <div className="flex gap-1">
            <button
              onClick={() => setUnreadOnly(false)}
              className={`rounded px-2 py-1 text-xs transition ${
                !unreadOnly ? "bg-panel2 text-ink" : "text-muted hover:text-ink"
              }`}
            >
              All
            </button>
            <button
              onClick={() => setUnreadOnly(true)}
              className={`rounded px-2 py-1 text-xs transition ${
                unreadOnly ? "bg-panel2 text-ink" : "text-muted hover:text-ink"
              }`}
            >
              Unread
            </button>
          </div>
        }
      >
        {error && <ErrorBox message={error} />}

        {loading ? (
          <Spinner />
        ) : items.length === 0 ? (
          <Empty
            title={unreadOnly ? "No unread notifications" : "No notifications yet"}
            hint="Alerts appear here when one of your orders fills, is cancelled or is rejected."
          />
        ) : (
          <ul className="divide-y divide-line">
            {items.map((n) => (
              <li key={n.id}>
                <button
                  onClick={() => markOne(n)}
                  className={`flex w-full items-start gap-3 px-1 py-3 text-left transition hover:bg-panel2 ${
                    n.is_read ? "opacity-60" : ""
                  }`}
                >
                  <span
                    className={`mt-0.5 w-5 shrink-0 text-center text-sm ${
                      NOTIFICATION_TONE[n.type] ?? "text-muted"
                    }`}
                    aria-hidden
                  >
                    {NOTIFICATION_ICON[n.type] ?? "•"}
                  </span>

                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="text-sm font-medium text-ink">{n.title}</span>
                      {n.symbol && (
                        <Link
                          href={`/market/${n.symbol}`}
                          onClick={(e) => e.stopPropagation()}
                          className="num rounded bg-panel2 px-1.5 py-0.5 text-[11px] text-accent hover:underline"
                        >
                          {n.symbol}
                        </Link>
                      )}
                    </span>
                    <span className="mt-0.5 block text-sm text-muted">{n.message}</span>
                  </span>

                  <span className="flex shrink-0 items-center gap-2 pt-0.5">
                    <span className="text-xs text-muted">{timeAgo(n.created_at)}</span>
                    {!n.is_read && <span className="h-2 w-2 rounded-full bg-accent" />}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {hasMore && !loading && (
          <div className="pt-3 text-center">
            <Button variant="ghost" onClick={loadMore} loading={loadingMore}>
              Load more
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}

export default function NotificationsPage() {
  return (
    <Protected>
      <NotificationsInner />
    </Protected>
  );
}
