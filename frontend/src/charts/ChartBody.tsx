import { useEffect, useRef } from "react";
import type { Run, Spec } from "../api";
import { DataTable, Stat } from "./Figures";
import { config, type Figure, figure } from "./plot";

// Plotly is large, so it is fetched only when the first chart is drawn.
const plotly = () => import("plotly.js-dist-min");

function Plot({ fig }: { fig: Figure }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let live = true;
    const element = ref.current;
    plotly().then((Plotly) => {
      if (live && element) Plotly.react(element, fig.data, fig.layout, config);
    });
    return () => {
      live = false;
    };
  }, [fig]);
  useEffect(() => {
    const element = ref.current;
    return () => {
      if (element) plotly().then((Plotly) => Plotly.purge(element));
    };
  }, []);
  return <div ref={ref} className="h-full w-full" />;
}

/** Draws one view of a chart's result. */
export function ChartBody({ spec, run, fig }: { spec: Spec; run: Run | null; fig: Figure | null }) {
  if (!run) return <p className="text-ink-muted">This chart has not been run yet.</p>;
  if (spec.kind === "table") return <DataTable run={run} />;
  if (spec.kind === "stat") return <Stat run={run} value={spec.value} caption={spec.label} />;
  return fig ? <Plot fig={fig} /> : null;
}

export { figure };
