import type { Cell, Column, Run } from "../api";
import { cell, seriesColor } from "./format";

export type Series = { name: string; color: string; values: (number | null)[] };

/** A result reshaped for a bar or line chart: one slot per distinct x value,
    in the order the query returned them, and one series per distinct value of
    the series column (or a single series named after y). */
export type Frame = {
  x: Column;
  y: Column;
  xs: Cell[];
  xLabels: string[];
  series: Series[];
  max: number;
  min: number;
};

export function frame(run: Run, xName: string, yName: string, seriesName?: string | null): Frame | null {
  const index = (name: string) => run.columns.findIndex((c) => c.name === name);
  const [xi, yi, si] = [index(xName), index(yName), seriesName ? index(seriesName) : -1];
  const x = run.columns[xi];
  const y = run.columns[yi];
  if (!x || !y) return null;

  const xs: Cell[] = [];
  const xAt = new Map<string, number>();
  const names: string[] = [];
  const cells = new Map<string, number | null>();
  const seriesColumn = run.columns[si];
  for (const row of run.rows) {
    const xValue = row[xi] ?? null;
    const xKey = String(xValue);
    if (!xAt.has(xKey)) {
      xAt.set(xKey, xs.length);
      xs.push(xValue);
    }
    const name = seriesColumn ? cell(row[si] ?? null, seriesColumn, true) : "";
    if (!names.includes(name)) names.push(name);
    const value = row[yi];
    cells.set(`${name}\u0000${xKey}`, typeof value === "number" ? value : null);
  }
  const series = names.map((name, i) => ({
    name,
    color: seriesColor(i),
    values: xs.map((xValue) => cells.get(`${name}\u0000${String(xValue)}`) ?? null),
  }));
  const all = series.flatMap((s) => s.values).filter((v): v is number => v !== null);
  const firstOfMonth =
    x.kind === "time" && xs.every((v) => typeof v === "string" && /-01($|T00:00:00)/.test(v));
  return {
    x,
    y,
    xs,
    xLabels: xs.map((v) =>
      firstOfMonth && typeof v === "string" ? monthLabel(v) : cell(v, x, true),
    ),
    series,
    max: Math.max(0, ...all),
    min: Math.min(0, ...all),
  };
}

function monthLabel(iso: string): string {
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const [y, m] = iso.split("-").map(Number);
  return `${names[(m ?? 1) - 1]} ${y}`;
}
