import type { Chart } from "../api";
import { Columns, HorizontalBars } from "./Bars";
import { frame } from "./data";
import { DataTable, Stat } from "./Figures";
import { Lines } from "./Lines";
import { useSize } from "./parts";

/** Draws a chart from its spec and latest result, or the result as a table. */
export function ChartBody({ chart, asTable }: { chart: Chart; asTable: boolean }) {
  const [ref, size] = useSize<HTMLDivElement>();
  const { spec, run } = chart;
  let body = null;
  if (!run) {
    body = <p className="text-ink-muted">This chart has not been run yet.</p>;
  } else if (asTable || spec.kind === "table") {
    body = <DataTable run={run} />;
  } else if (spec.kind === "stat") {
    body = <Stat run={run} value={spec.value} caption={spec.label} />;
  } else if (size.width > 0) {
    const data = frame(run, spec.x, spec.y, spec.series);
    const legend = data && data.series.length > 1 ? 32 : 0;
    const height = Math.max(size.height - legend, 220);
    if (!data) body = <DataTable run={run} />;
    else if (spec.kind === "line") body = <Lines frame={data} width={size.width} height={height} />;
    else if (data.x.kind === "text" || data.x.kind === "boolean") body = <HorizontalBars frame={data} width={size.width} />;
    else body = <Columns frame={data} width={size.width} height={height} />;
  }
  return (
    <div ref={ref} className="h-full min-h-0 w-full min-w-0 overflow-x-hidden overflow-y-auto">
      {body}
    </div>
  );
}
