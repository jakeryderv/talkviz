import type { Cell, Column } from "../api";

const full = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "brake_complaints" -> "Brake complaints". */
export function label(name: string): string {
  const words = name.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A number in full: 12,431 or 0.14. */
export function number(value: number): string {
  if (value !== 0 && Math.abs(value) < 1) return String(Number(value.toPrecision(3)));
  return full.format(value);
}

/** Axis ticks: in full below 10,000, compact (12K) on an axis that reaches it. */
export function tickFormat(max: number): (value: number) => string {
  return Math.abs(max) >= 10000 ? (v) => compact.format(v) : number;
}

/** A headline figure: 1,284 / 12.9K / 4.2M. */
export function headline(value: number): string {
  return Math.abs(value) >= 100000 ? compact.format(value) : number(value);
}

/** ISO date or timestamp -> "Mar 2024", "9 Mar 2024", or with the time. */
export function date(iso: string, allFirstOfMonth = false): string {
  const [day = "", clock] = iso.split("T");
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return iso;
  if (allFirstOfMonth) return `${months[m - 1]} ${y}`;
  const text = `${d} ${months[m - 1]} ${y}`;
  return clock && !clock.startsWith("00:00:00") ? `${text} ${clock.slice(0, 5)}` : text;
}

/** A cell as text, for tables, tooltips, and category labels. */
export function cell(value: Cell, column: Column, asCategory = false): string {
  if (value === null) return "–";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (column.kind === "time" && typeof value === "string") return date(value);
  // Whole numbers used as categories are usually years: no thousands separator.
  if (typeof value === "number") {
    return asCategory && Number.isInteger(value) ? String(value) : number(value);
  }
  // Category labels read better without code-style underscores ("air_bags").
  return asCategory ? String(value).replace(/_/g, " ") : String(value);
}

export function seriesColor(index: number): string {
  return `var(--color-series-${(index % 8) + 1})`;
}
