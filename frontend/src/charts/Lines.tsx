import { scaleLinear } from "d3-scale";
import { area, line } from "d3-shape";
import { useState } from "react";
import type { Frame } from "./data";
import { number, tickFormat } from "./format";
import { Legend, thin, Tooltip } from "./parts";

/** Lines over an ordered axis. A crosshair snaps to the nearest x and reads
    out every series there. */
export function Lines({ frame, width, height }: { frame: Frame; width: number; height: number }) {
  const [at, setAt] = useState<number | null>(null);
  const margin = { top: 12, right: 16, bottom: 28, left: 48 };
  const position = frame.xs.map((v) =>
    frame.x.kind === "time" ? Date.parse(String(v)) : Number(v),
  );
  const x = scaleLinear()
    .domain([Math.min(...position), Math.max(...position)])
    .range([margin.left, width - margin.right]);
  const y = scaleLinear().domain([frame.min, frame.max]).nice(5).range([height - margin.bottom, margin.top]);
  const format = tickFormat(frame.max);
  const shown = thin(frame.xLabels, width - margin.left - margin.right);
  const px = (i: number) => x(position[i] ?? 0);
  const path = line<number | null>()
    .defined((v) => v != null)
    .x((_, i) => px(i))
    .y((v) => y(v ?? 0));
  const wash = area<number | null>()
    .defined((v) => v != null)
    .x((_, i) => px(i))
    .y0(y(0))
    .y1((v) => y(v ?? 0));
  const single = frame.series.length === 1 ? frame.series[0] : undefined;

  const nearest = (clientX: number, svg: SVGSVGElement) => {
    const pointer = clientX - svg.getBoundingClientRect().left;
    let best = 0;
    for (let i = 1; i < position.length; i++) {
      if (Math.abs(px(i) - pointer) < Math.abs(px(best) - pointer)) best = i;
    }
    return best;
  };
  const rows =
    at === null
      ? []
      : frame.series
          .filter((s) => s.values[at] != null)
          .sort((a, b) => (b.values[at] as number) - (a.values[at] as number))
          .map((s) => ({ name: s.name, color: s.color, value: number(s.values[at] as number) }));

  return (
    <div className="relative">
      <Legend series={frame.series} marks="line" />
      <svg
        width={width}
        height={height}
        role="img"
        className="block"
        onPointerMove={(e) => setAt(nearest(e.clientX, e.currentTarget))}
        onPointerLeave={() => setAt(null)}
      >
        {y.ticks(5).map((t) => (
          <g key={t}>
            <line x1={margin.left} x2={width - margin.right} y1={y(t)} y2={y(t)} className="stroke-line" />
            <text x={margin.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-ink-faint text-[11px] tabular-nums">
              {format(t)}
            </text>
          </g>
        ))}
        <line x1={margin.left} x2={width - margin.right} y1={y(0)} y2={y(0)} className="stroke-line-strong" />
        {frame.xLabels.map(
          (text, i) =>
            shown.has(i) && (
              <text key={i} x={px(i)} y={height - 8} textAnchor="middle" className="fill-ink-faint text-[11px] tabular-nums">
                {text}
              </text>
            ),
        )}
        {at !== null && (
          <line x1={px(at)} x2={px(at)} y1={margin.top} y2={height - margin.bottom} className="stroke-line-strong" />
        )}
        {single && <path d={wash(single.values) ?? ""} fill={single.color} opacity={0.1} />}
        {frame.series.map((s) => {
          const last = s.values.findLastIndex((v) => v != null);
          return (
            <g key={s.name}>
              <path d={path(s.values) ?? ""} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {last >= 0 && at === null && (
                <circle cx={px(last)} cy={y(s.values[last] as number)} r={4} fill={s.color} className="stroke-surface" strokeWidth={2} />
              )}
              {at !== null && s.values[at] != null && (
                <circle cx={px(at)} cy={y(s.values[at] as number)} r={4} fill={s.color} className="stroke-surface" strokeWidth={2} />
              )}
            </g>
          );
        })}
      </svg>
      <Tooltip
        tip={at === null ? null : { x: px(at), y: margin.top + 36, title: frame.xLabels[at] ?? "", rows }}
        width={width}
      />
    </div>
  );
}
