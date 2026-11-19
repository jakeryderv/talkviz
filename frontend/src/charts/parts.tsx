import { useLayoutEffect, useRef, useState } from "react";
import type { Series } from "./data";

/** The element's content size, kept up to date as it resizes. */
export function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, size] as const;
}

export type Tip = { x: number; y: number; title: string; rows: { name: string; color: string; value: string }[] };

/** Hover readout: the value leads, the series name follows. */
export function Tooltip({ tip, width }: { tip: Tip | null; width: number }) {
  if (!tip) return null;
  const flip = tip.x > width - 220;
  return (
    <div
      role="status"
      className="pointer-events-none absolute z-10 min-w-36 rounded-md border border-line-strong bg-raised px-3 py-2 shadow-xl shadow-black/40"
      style={{ left: tip.x, top: tip.y, transform: `translate(${flip ? "calc(-100% - 14px)" : "14px"}, -50%)` }}
    >
      <div className="mb-1 text-xs text-ink-muted">{tip.title}</div>
      {tip.rows.map((row) => (
        <div key={row.name} className="flex items-center gap-2 whitespace-nowrap">
          <span className="h-0.5 w-3 shrink-0 rounded-full" style={{ background: row.color }} />
          <span className="font-semibold text-ink-strong tabular-nums">{row.value}</span>
          {row.name && <span className="text-xs text-ink-muted">{row.name}</span>}
        </div>
      ))}
    </div>
  );
}

/** Shown for two or more series, so identity never rests on colour alone. */
export function Legend({ series, marks }: { series: Series[]; marks: "line" | "bar" }) {
  if (series.length < 2) return null;
  return (
    <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
      {series.map((s) => (
        <li key={s.name} className="flex items-center gap-1.5">
          <span
            className={marks === "line" ? "h-0.5 w-3.5 rounded-full" : "size-2.5 rounded-xs"}
            style={{ background: s.color }}
          />
          {s.name}
        </li>
      ))}
    </ul>
  );
}

/** Indices of the labels to draw so that neighbours do not collide. */
export function thin(labels: string[], available: number, charWidth = 6.5): Set<number> {
  const widest = Math.max(...labels.map((l) => l.length), 1) * charWidth + 16;
  const step = Math.max(1, Math.ceil((labels.length * widest) / Math.max(available, 1)));
  const keep = new Set<number>();
  for (let i = 0; i < labels.length; i += step) keep.add(i);
  return keep;
}
