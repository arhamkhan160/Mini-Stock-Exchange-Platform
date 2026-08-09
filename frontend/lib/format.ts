// Money arrives as 4-decimal STRINGS. Convert to Number for DISPLAY ONLY —
// never for a value you send back to the server.

export const num = (v: string | number | null | undefined): number => {
  const n = typeof v === "number" ? v : Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
};

/** "1234.5000" -> "$1,234.50" */
export function money(v: string | number | null | undefined, dp = 2): string {
  return num(v).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });
}

/** "195.5000" -> "195.50" (no currency symbol, for book/chart tables) */
export function price(v: string | number | null | undefined, dp = 2): string {
  return num(v).toFixed(dp);
}

export function qty(v: number | null | undefined): string {
  return (v ?? 0).toLocaleString("en-US");
}

export function pct(v: number | null | undefined, dp = 2): string {
  const n = v ?? 0;
  return `${n >= 0 ? "+" : ""}${n.toFixed(dp)}%`;
}

export function signed(v: string | number | null | undefined, dp = 2): string {
  const n = num(v);
  return `${n >= 0 ? "+" : "-"}${money(Math.abs(n), dp).replace("-", "")}`;
}

/** Tailwind class for a gain/loss value. */
export function toneOf(v: string | number | null | undefined): string {
  const n = num(v);
  if (n > 0) return "text-up";
  if (n < 0) return "text-down";
  return "text-muted";
}

export function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", { hour12: false });
}

/* ---- client-side validation that MIRRORS the backend rules exactly ---- */

/** price > 0 and a multiple of the 0.01 tick */
export function validatePrice(raw: string): string | null {
  if (!raw.trim()) return "Price is required";
  if (!/^\d+(\.\d{1,2})?$/.test(raw.trim())) return "Price must have at most 2 decimals";
  if (Number(raw) <= 0) return "Price must be greater than 0";
  if (Number(raw) > 1_000_000) return "Price is too large";
  return null;
}

/** whole shares, > 0 */
export function validateQuantity(raw: string): string | null {
  if (!raw.trim()) return "Quantity is required";
  if (!/^\d+$/.test(raw.trim())) return "Quantity must be a whole number of shares";
  const n = Number(raw);
  if (n <= 0) return "Quantity must be greater than 0";
  if (n > 1_000_000) return "Quantity is too large";
  return null;
}

export function validateAmount(raw: string): string | null {
  if (!raw.trim()) return "Amount is required";
  if (!/^\d+(\.\d{1,2})?$/.test(raw.trim())) return "Amount must have at most 2 decimals";
  const n = Number(raw);
  if (n <= 0) return "Amount must be greater than 0";
  if (n > 1_000_000) return "Maximum deposit is 1,000,000";
  return null;
}
