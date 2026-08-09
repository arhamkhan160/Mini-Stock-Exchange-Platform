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
    <header className="sticky top-0 z-40 border-b border-line bg-panel/95 backdrop-blur">
      <nav className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4">
        <Link href="/" className="text-sm font-bold tracking-wide text-ink">
          MSE<span className="text-accent">·</span>Exchange
        </Link>

        {user && (
          <ul className="flex items-center gap-1">
            {LINKS.map((l) => (
              <li key={l.href}>
                <Link
                  href={l.href}
                  className={`rounded-md px-3 py-1.5 text-sm transition ${
                    path === l.href ? "bg-panel2 text-ink" : "text-muted hover:text-ink"
                  }`}
                >
                  {l.label}
                </Link>
              </li>
            ))}
          </ul>
        )}

        <div className="ml-auto flex items-center gap-3">
          {user ? (
            <>
              {available !== null && (
                <span className="num hidden text-sm text-muted sm:inline">
                  Available <span className="text-ink">{money(available)}</span>
                </span>
              )}
              <NotificationBell />
              <span className="hidden text-sm text-muted md:inline">{user.username}</span>
              <Button variant="ghost" onClick={logout}>Sign out</Button>
            </>
          ) : ready ? (
            <>
              <Link href="/login" className="text-sm text-muted hover:text-ink">Sign in</Link>
              <Link href="/register"><Button>Create account</Button></Link>
            </>
          ) : null}
        </div>
      </nav>
    </header>
  );
}
