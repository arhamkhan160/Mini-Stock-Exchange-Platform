"use client";

// Shared primitives. Four people build pages — everyone uses THESE so the app
// looks like one product. Do not hand-roll new button/card styles in a page.

import type { ReactNode } from "react";
import { toneOf } from "@/lib/format";

export function Card({
  title, right, children, className = "",
}: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-line bg-panel ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold tracking-wide text-ink">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "buy" | "sell" | "ghost" | "danger";
  loading?: boolean;
};

export function Button({ variant = "primary", loading, disabled, className = "", children, ...rest }: BtnProps) {
  const styles: Record<string, string> = {
    primary: "bg-accent hover:bg-accent/90 text-white",
    buy: "bg-up hover:bg-up/90 text-white",
    sell: "bg-down hover:bg-down/90 text-white",
    ghost: "bg-panel2 hover:bg-line text-ink border border-line",
    danger: "bg-transparent hover:bg-down/10 text-down border border-down/40",
  };
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={`rounded-md px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles[variant]} ${className}`}
    >
      {loading ? "Working…" : children}
    </button>
  );
}

export function Input({
  label, error, hint, className = "", ...rest
}: React.InputHTMLAttributes<HTMLInputElement> & { label?: string; error?: string | null; hint?: ReactNode }) {
  return (
    <label className="block">
      {label && <span className="mb-1 block text-xs font-medium text-muted">{label}</span>}
      <input
        {...rest}
        className={`num w-full rounded-md border bg-bg px-3 py-2 text-sm text-ink outline-none transition
          ${error ? "border-down" : "border-line focus:border-accent"} ${className}`}
      />
      {error ? (
        <span className="mt-1 block text-xs text-down">{error}</span>
      ) : hint ? (
        <span className="mt-1 block text-xs text-muted">{hint}</span>
      ) : null}
    </label>
  );
}

export function Select({
  label, className = "", children, ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement> & { label?: string }) {
  return (
    <label className="block">
      {label && <span className="mb-1 block text-xs font-medium text-muted">{label}</span>}
      <select
        {...rest}
        className={`w-full rounded-md border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent ${className}`}
      >
        {children}
      </select>
    </label>
  );
}

const BADGE: Record<string, string> = {
  PENDING: "bg-muted/15 text-muted",
  NEW: "bg-accent/15 text-accent",
  PARTIALLY_FILLED: "bg-warn/15 text-warn",
  FILLED: "bg-up/15 text-up",
  CANCEL_PENDING: "bg-warn/15 text-warn",
  CANCELLED: "bg-muted/15 text-muted",
  REJECTED: "bg-down/15 text-down",
  BUY: "bg-up/15 text-up",
  SELL: "bg-down/15 text-down",
};

const LABEL: Record<string, string> = {
  PARTIALLY_FILLED: "PARTIAL",
  CANCEL_PENDING: "CANCELLING…",
};

export function Badge({ value }: { value: string }) {
  return (
    <span className={`rounded px-2 py-0.5 text-[11px] font-semibold ${BADGE[value] ?? "bg-panel2 text-muted"}`}>
      {LABEL[value] ?? value}
    </span>
  );
}

/** Value coloured by sign. Pass a money string straight from the API. */
export function Tone({ value, children }: { value: string | number; children: ReactNode }) {
  return <span className={`num ${toneOf(value)}`}>{children}</span>;
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-10 text-center">
      <p className="text-sm text-ink">{title}</p>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return <div className="py-8 text-center text-sm text-muted">{label}</div>;
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">{message}</div>
  );
}

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
            {head.map((h) => (
              <th key={h} className="px-3 py-2 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
