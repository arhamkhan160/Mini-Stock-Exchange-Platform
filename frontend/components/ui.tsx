"use client";

// Shared primitives. Four people build pages — everyone uses THESE so the app
// looks like one product. Do not hand-roll new button/card styles in a page.

import type { ReactNode } from "react";
import { money, num, toneOf } from "@/lib/format";

/* ------------------------------------------------------------------ layout */

/**
 * A panel.
 *
 * `Card` owns its own padding. Callers used to fight that by passing
 * `className="p-4"` (doubling it) or `className="p-0"` (to host a table), so the
 * body padding is now a prop: `padded={false}` for a flush table or chart, and
 * `className` is left for the OUTER box only (width, grid span, margins).
 */
export function Card({
  title,
  subtitle,
  right,
  children,
  className = "",
  padded = true,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section className={`overflow-hidden rounded-lg border border-line bg-panel shadow-card ${className}`}>
      {(title || right) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold tracking-wide text-ink">{title}</h2>
            {subtitle && <p className="mt-0.5 truncate text-xs text-muted">{subtitle}</p>}
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </header>
      )}
      <div className={padded ? "p-4" : ""}>{children}</div>
    </section>
  );
}

/** One page title, one size, everywhere. */
export function PageHeader({
  title,
  subtitle,
  right,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold text-ink">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  );
}

/**
 * A single headline figure.
 *
 * The dashboard and the portfolio both showed four KPI tiles, built by hand in
 * two different ways. This is the one implementation. `tone` colours the value
 * by its sign (for P&L); leave it off for a neutral figure like cash.
 */
export function Stat({
  label,
  value,
  tone = false,
  hint,
  loading = false,
}: {
  label: ReactNode;
  value: string | number | null | undefined;
  tone?: boolean;
  hint?: ReactNode;
  loading?: boolean;
}) {
  const empty = value === null || value === undefined;
  const toneClass = tone && !empty ? toneOf(value) : "text-ink";
  const sign = tone && !empty && num(value) > 0 ? "+" : "";

  return (
    <div className="rounded-lg border border-line bg-panel p-4 shadow-card">
      <p className="text-2xs font-medium uppercase tracking-wider text-faint">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-28" />
      ) : (
        <p className={`num mt-1.5 text-2xl font-semibold ${empty ? "text-faint" : toneClass}`}>
          {empty ? "—" : `${sign}${money(value)}`}
        </p>
      )}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

/* ----------------------------------------------------------------- controls */

type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "buy" | "sell" | "ghost" | "danger";
  size?: "sm" | "md";
  loading?: boolean;
};

export function Button({
  variant = "primary",
  size = "md",
  loading,
  disabled,
  className = "",
  children,
  ...rest
}: BtnProps) {
  const styles: Record<string, string> = {
    primary: "bg-accent hover:bg-accent/90 active:bg-accent/80 text-white",
    buy: "bg-up hover:bg-up/90 active:bg-up/80 text-white",
    sell: "bg-down hover:bg-down/90 active:bg-down/80 text-white",
    ghost: "bg-panel2 hover:bg-panel3 active:bg-line text-ink border border-line",
    danger: "bg-transparent hover:bg-down/10 text-down border border-down/40",
  };
  const sizes: Record<string, string> = {
    sm: "px-2.5 py-1 text-xs",
    md: "px-4 py-2 text-sm",
  };
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-md font-medium transition
        disabled:cursor-not-allowed disabled:opacity-50
        ${styles[variant]} ${sizes[size]} ${className}`}
    >
      {loading && <Spinner inline />}
      {loading ? "Working…" : children}
    </button>
  );
}

export function Input({
  label,
  error,
  hint,
  className = "",
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement> & {
  label?: string;
  error?: string | null;
  hint?: ReactNode;
}) {
  return (
    <label className="block">
      {label && <span className="mb-1.5 block text-xs font-medium text-muted">{label}</span>}
      <input
        {...rest}
        aria-invalid={error ? true : undefined}
        className={`num w-full rounded-md border bg-bg px-3 py-2 text-sm text-ink outline-none transition
          placeholder:text-faint
          ${error ? "border-down" : "border-line hover:border-line2 focus:border-accent"} ${className}`}
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
  label,
  className = "",
  children,
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement> & { label?: string }) {
  return (
    <label className="block">
      {label && <span className="mb-1.5 block text-xs font-medium text-muted">{label}</span>}
      <select
        {...rest}
        className={`w-full rounded-md border border-line bg-bg px-3 py-2 text-sm text-ink outline-none
          transition hover:border-line2 focus:border-accent ${className}`}
      >
        {children}
      </select>
    </label>
  );
}

/** A segmented control — the chart's 1m/5m switch, and anything like it. */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  label?: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-md border border-line bg-panel2 p-0.5">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(o.value)}
            className={`rounded px-2.5 py-1 text-xs font-medium transition ${
              active ? "bg-accent text-white" : "text-muted hover:text-ink"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------- indicators */

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

const VARIANT: Record<string, string> = {
  success: "bg-up/15 text-up",
  warning: "bg-warn/15 text-warn",
  danger: "bg-down/15 text-down",
  info: "bg-accent/15 text-accent",
};

/** Either a domain value (`<Badge value="FILLED" />`) or free content with an
 *  explicit tone (`<Badge variant="success">Live</Badge>`). */
export function Badge({
  value,
  variant,
  children,
}: {
  value?: string;
  variant?: "success" | "warning" | "danger" | "info";
  children?: ReactNode;
}) {
  const tone = variant ? VARIANT[variant] : BADGE[value ?? ""] ?? "bg-panel2 text-muted";
  const label = children ?? LABEL[value ?? ""] ?? value;
  return (
    <span className={`inline-block whitespace-nowrap rounded px-2 py-0.5 text-2xs font-semibold ${tone}`}>
      {label}
    </span>
  );
}

/** Connection state. Colour alone never carries it — the text is always there. */
export function LiveDot({ status }: { status: string }) {
  const live = status === "live";
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted">
      <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-up" : "bg-warn"}`} />
      {live ? "Live" : status === "connecting" ? "Connecting…" : "Reconnecting…"}
    </span>
  );
}

/**
 * A value coloured by its sign.
 *
 * Pass `children` to render your own content, or a `format` and let Tone do it
 * — it already knows the sign, so it is the natural place to put the +/- and
 * the fixed decimals.
 */
export function Tone({
  value,
  format,
  prefix,
  children,
}: {
  value: string | number;
  format?: "money" | "pct" | "price";
  prefix?: string;
  children?: ReactNode;
}) {
  const className = `num ${toneOf(value)}`;
  if (children !== undefined) return <span className={className}>{children}</span>;

  const n = num(value);
  const magnitude = Math.abs(n);
  const sign = n > 0 ? "+" : n < 0 ? "-" : "";
  const body =
    format === "pct"
      ? `${magnitude.toFixed(2)}%`
      : `${prefix ?? ""}${magnitude.toLocaleString("en-US", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })}`;
  return (
    <span className={className}>
      {sign}
      {body}
    </span>
  );
}

/* ----------------------------------------------------------------- feedback */

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={`animate-shimmer rounded bg-[linear-gradient(90deg,theme(colors.panel2)_25%,theme(colors.line)_50%,theme(colors.panel2)_75%)] bg-[length:200%_100%] ${className}`}
    />
  );
}

export function Empty({
  title,
  hint,
  subtitle,
  action,
}: {
  title: string;
  hint?: string;
  /** Alias for `hint`. */
  subtitle?: string;
  action?: ReactNode;
}) {
  const detail = hint ?? subtitle;
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 px-4 py-12 text-center">
      <div aria-hidden className="mb-1 h-8 w-8 rounded-full border border-dashed border-line2" />
      <p className="text-sm font-medium text-ink">{title}</p>
      {detail && <p className="max-w-xs text-xs text-muted">{detail}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

/** `inline` for inside a button or a sentence; otherwise a centred block. */
export function Spinner({ label = "Loading…", inline = false }: { label?: string; inline?: boolean }) {
  const ring = (
    <span
      aria-hidden
      className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
    />
  );

  if (inline) return ring;

  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted">
      <span className="text-muted">{ring}</span>
      <span>{label}</span>
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="animate-fade-in rounded-md border border-down/40 bg-down/10 px-3 py-2 text-sm text-down"
    >
      {message}
    </div>
  );
}

/* -------------------------------------------------------------------- table */

/**
 * Pass `head` and Table renders the header row and wraps children in a tbody.
 * Omit it to supply your own `<thead>`/`<tbody>` as children.
 *
 * `align` marks which columns are numeric so they can be right-aligned from one
 * place — a numeric column that is not right-aligned is the single most common
 * way a data table stops being scannable.
 */
export function Table({
  head,
  align = [],
  children,
}: {
  head?: string[];
  align?: ("left" | "right")[];
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        {head ? (
          <>
            <thead>
              <tr className="border-b border-line text-left text-2xs uppercase tracking-wider text-faint">
                {head.map((h, i) => (
                  <th
                    key={h || i}
                    scope="col"
                    className={`px-3 py-2.5 font-medium ${align[i] === "right" ? "text-right" : "text-left"}`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>{children}</tbody>
          </>
        ) : (
          children
        )}
      </table>
    </div>
  );
}

/** The standard body row — one hover treatment for every table in the app. */
export function Row({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <tr className={`border-b border-line/60 transition-colors last:border-0 hover:bg-panel2 ${className}`}>
      {children}
    </tr>
  );
}
