import type { EntityKind, NodeStatus } from "./types";

export const KIND_COLOR: Record<EntityKind, string> = {
  Supplier: "#60a5fa",
  Part: "#a78bfa",
  Product: "#34d399",
  Customer: "#fbbf24",
  Region: "#94a3b8",
  Port: "#22d3ee",
  Disruption: "#f87171",
};

export const STATUS_COLOR: Record<NodeStatus, string> = {
  normal: "transparent",
  affected: "#f59e0b",
  degraded: "#f59e0b",
  disrupted: "#ef4444",
  disruption: "#ef4444",
};

export function kindColor(kind: string): string {
  return KIND_COLOR[kind as EntityKind] ?? "#94a3b8";
}

const SYMBOL: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", JPY: "¥", INR: "₹", CNY: "¥" };

/** Compact money formatting that matches the backend's `*_display` strings. */
export function money(value: number | null | undefined, currency: string): string {
  const v = Number(value ?? 0);
  const prefix = SYMBOL[currency] ?? `${currency} `;
  for (const [div, suffix] of [
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ] as const) {
    if (Math.abs(v) >= div) return `${prefix}${(v / div).toFixed(2)}${suffix}`;
  }
  return `${prefix}${Math.round(v).toLocaleString()}`;
}

export function pct(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

export function humanize(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function bytes(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} KB`;
  return `${n} B`;
}
