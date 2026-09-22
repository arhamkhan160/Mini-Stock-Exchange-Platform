"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { AccountAPI } from "@/lib/api";
import { money } from "@/lib/format";
import { Button } from "./ui";
import NotificationBell from "./NotificationBell";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/orders", label: "Orders" },
  { href: "/portfolio", label: "Portfolio" },
  { href: "/wallet", label: "Wallet" },
];

export default function Navbar() {
  const { user, ready, logout } = useAuth();
  const path = usePathname();
  const [available, setAvailable] = useState<string | null>(null);

  useEffect(() => {
    if (!user) { setAvailable(null); return; }
    let alive = true;
    const load = () =>
      AccountAPI.balance()
        .then((b) => alive && setAvailable(b.available_balance))
        .catch(() => {});
    load();
    const t = setInterval(load, 10000);
    return () => { alive = false; clearInterval(t); };
  }, [user]);

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-panel/90 backdrop-blur-md">
      <nav className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4 sm:px-6">
        <Link
          href="/"
          className="shrink-0 text-sm font-bold tracking-wide text-ink transition hover:text-accent"
        >
          MSE<span className="text-accent">·</span>Exchange
        </Link>

        {user && (
          <ul className="flex items-center gap-0.5">
            {LINKS.map((l) => {
              const active = path === l.href;
              return (
                <li key={l.href}>
                  <Link
                    href={l.href}
                    aria-current={active ? "page" : undefined}
                    className={`relative rounded-md px-3 py-1.5 text-sm transition ${
                      active
                        ? "bg-panel2 font-medium text-ink"
                        : "text-muted hover:bg-panel2/60 hover:text-ink"
                    }`}
                  >
                    {l.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        )}

        <div className="ml-auto flex items-center gap-3">
          {user ? (
            <>
              {available !== null && (
                <span className="hidden items-center gap-1.5 text-xs text-muted sm:flex">
                  <span className="uppercase tracking-wider text-faint">Available</span>
                  <span className="num text-sm text-ink">{money(available)}</span>
                </span>
              )}
              <NotificationBell />
              <span className="hidden text-sm text-muted md:inline">{user.username}</span>
              <Button variant="ghost" size="sm" onClick={logout}>Sign out</Button>
            </>
          ) : ready ? (
            <>
              <Link href="/login" className="text-sm text-muted transition hover:text-ink">Sign in</Link>
              <Link href="/register"><Button size="sm">Create account</Button></Link>
            </>
          ) : null}
        </div>
      </nav>
    </header>
  );
}
