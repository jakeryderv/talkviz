import { scaleBand, scaleLinear } from "d3-scale";
import { useState } from "react";
import type { Frame } from "./data";
import { number, tickFormat } from "./format";
import { Legend, thin, type Tip, Tooltip } from "./parts";

const GAP = 2; // surface gap between touching bars

function tipFor(frame: Frame, i: number, x: number, y: number): Tip {
  return {
    x,
    y,
    title: frame.xLabels[i] ?? "",
    rows: frame.series
      .filter((s) => s.values[i] != null)
      .map((s) => ({ name: s.name, color: s.color, value: number(s.values[i] as number) })),
  };
}

/** Horizontal bars, for named categories: labels read left to right. */
export function HorizontalBars({ frame, width }: { frame: Frame; width: number }) {
  const [hover, setHover] = useState<{ i: number; tip: Tip } | null>(null);
  const k = frame.series.length;
  const thick = k === 1 ? 20 : 12;
  const rowHeight = k * thick + (k - 1) * GAP + 14;
  const gutter = Math.min(Math.max(...frame.xLabels.map((l) => l.length)) * 7 + 16, width * 0.34, 220);
  const right = k === 1 ? 64 : 16;
  const axis = 24;
  const height = frame.xs.length * rowHeight + axis;
  const scale = scaleLinear().domain([frame.min, frame.max]).nice(5).range([gutter, width - right]);
  const ticks = scale.ticks(5);
  const format = tickFormat(frame.max);
  const zero = scale(0);
  const maxChars = Math.floor((gutter - 16) / 6.6);

  return (
    <div className="relative">
      <Legend series={frame.series} marks="bar" />
      <svg width={width} height={height} role="img" className="block" onPointerLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={scale(t)} x2={scale(t)} y1={0} y2={height - axis} className="stroke-line" />
            <text x={scale(t)} y={height - 6} textAnchor="middle" className="fill-ink-faint text-[11px] tabular-nums">
              {format(t)}
            </text>
          </g>
        ))}
        {frame.xs.map((_, i) => {
          const top = i * rowHeight;
          const name = frame.xLabels[i] ?? "";
          return (
            <g
              key={i}
              tabIndex={0}
              className="outline-none"
              onPointerMove={(e) => {
                const box = e.currentTarget.ownerSVGElement!.getBoundingClientRect();
                setHover({ i, tip: tipFor(frame, i, e.clientX - box.left, top + rowHeight / 2) });
              }}
              onFocus={() => setHover({ i, tip: tipFor(frame, i, zero + 40, top + rowHeight / 2) })}
              onBlur={() => setHover(null)}
            >
              <rect x={0} y={top} width={width} height={rowHeight} className={hover?.i === i ? "fill-raised/60" : "fill-transparent"} />
              <text x={gutter - 10} y={top + rowHeight / 2} dy="0.35em" textAnchor="end" className="fill-ink text-xs">
                {name.length > maxChars ? `${name.slice(0, Math.max(maxChars - 1, 1))}…` : name}
                <title>{name}</title>
              </text>
              {frame.series.map((s, j) => {
                const value = s.values[i];
                if (value == null) return null;
                const y = top + 7 + j * (thick + GAP);
                const end = scale(value);
                const [x0, x1] = [Math.min(zero, end), Math.max(zero, end)];
                const r = Math.min(4, (x1 - x0) / 2);
                // Rounded at the data end, square at the baseline.
                const d =
                  value >= 0
                    ? `M${x0},${y}H${x1 - r}Q${x1},${y} ${x1},${y + r}V${y + thick - r}Q${x1},${y + thick} ${x1 - r},${y + thick}H${x0}Z`
                    : `M${x1},${y}H${x0 + r}Q${x0},${y} ${x0},${y + r}V${y + thick - r}Q${x0},${y + thick} ${x0 + r},${y + thick}H${x1}Z`;
                return (
                  <g key={s.name}>
                    <path d={d} fill={s.color} opacity={hover && hover.i !== i ? 0.55 : 1} className="transition-opacity" />
                    {k === 1 && (
                      <text x={x1 + 8} y={y + thick / 2} dy="0.35em" className="fill-ink-muted text-[11px] tabular-nums">
                        {number(value)}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          );
        })}
        <line x1={zero} x2={zero} y1={0} y2={height - axis} className="stroke-line-strong" />
      </svg>
      <Tooltip tip={hover?.tip ?? null} width={width} />
    </div>
  );
}

/** Vertical columns, for an ordered axis such as years. */
export function Columns({ frame, width, height }: { frame: Frame; width: number; height: number }) {
  const [hover, setHover] = useState<{ i: number; tip: Tip } | null>(null);
  const k = frame.series.length;
  const margin = { top: 12, right: 8, bottom: 28, left: 48 };
  const y = scaleLinear().domain([frame.min, frame.max]).nice(5).range([height - margin.bottom, margin.top]);
  const band = scaleBand<number>()
    .domain(frame.xs.map((_, i) => i))
    .range([margin.left, width - margin.right])
    .paddingInner(0.25)
    .paddingOuter(0.15);
  const thick = Math.max(2, Math.min(24, (band.bandwidth() - (k - 1) * GAP) / k));
  const group = k * thick + (k - 1) * GAP;
  const format = tickFormat(frame.max);
  const shown = thin(frame.xLabels, width - margin.left - margin.right);
  const zero = y(0);

  return (
    <div className="relative">
      <Legend series={frame.series} marks="bar" />
      <svg width={width} height={height} role="img" className="block" onPointerLeave={() => setHover(null)}>
        {y.ticks(5).map((t) => (
          <g key={t}>
            <line x1={margin.left} x2={width - margin.right} y1={y(t)} y2={y(t)} className="stroke-line" />
            <text x={margin.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-ink-faint text-[11px] tabular-nums">
              {format(t)}
            </text>
          </g>
        ))}
        {frame.xs.map((_, i) => {
          const left = (band(i) ?? 0) + (band.bandwidth() - group) / 2;
          const centre = (band(i) ?? 0) + band.bandwidth() / 2;
          return (
            <g
              key={i}
              tabIndex={0}
              className="outline-none"
              onPointerMove={(e) => {
                const box = e.currentTarget.ownerSVGElement!.getBoundingClientRect();
                setHover({ i, tip: tipFor(frame, i, centre, e.clientY - box.top) });
              }}
              onFocus={() => setHover({ i, tip: tipFor(frame, i, centre, height / 2) })}
              onBlur={() => setHover(null)}
            >
              <rect x={band(i) ?? 0} y={margin.top} width={band.bandwidth()} height={height - margin.top - margin.bottom} className={hover?.i === i ? "fill-raised/60" : "fill-transparent"} />
              {frame.series.map((s, j) => {
                const value = s.values[i];
                if (value == null) return null;
                const x0 = left + j * (thick + GAP);
                const end = y(value);
                const [top, bottom] = [Math.min(zero, end), Math.max(zero, end)];
                const r = Math.min(4, thick / 2, (bottom - top) / 2);
                const d =
                  value >= 0
                    ? `M${x0},${bottom}V${top + r}Q${x0},${top} ${x0 + r},${top}H${x0 + thick - r}Q${x0 + thick},${top} ${x0 + thick},${top + r}V${bottom}Z`
                    : `M${x0},${top}V${bottom - r}Q${x0},${bottom} ${x0 + r},${bottom}H${x0 + thick - r}Q${x0 + thick},${bottom} ${x0 + thick},${bottom - r}V${top}Z`;
                return <path key={s.name} d={d} fill={s.color} opacity={hover && hover.i !== i ? 0.55 : 1} className="transition-opacity" />;
              })}
              {shown.has(i) && (
                <text x={centre} y={height - 8} textAnchor="middle" className="fill-ink-faint text-[11px] tabular-nums">
                  {frame.xLabels[i]}
                </text>
              )}
            </g>
          );
        })}
        <line x1={margin.left} x2={width - margin.right} y1={zero} y2={zero} className="stroke-line-strong" />
      </svg>
      <Tooltip tip={hover?.tip ?? null} width={width} />
    </div>
  );
}
