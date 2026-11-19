// Turns a chart spec and its result into a Plotly figure. The spec only names
// columns; everything drawn comes from the result rows.

import type { Config, Data, Layout } from "plotly.js";
import type { Cell, Column, Run, Spec } from "../api";
import { cell, label, token } from "./format";

export type Figure = {
  data: Data[];
  layout: Partial<Layout>;
  /** What the drawing leaves out or groups, if anything. The table has every row. */
  note?: string;
};

// Readability limits. Past these the chart draws what fits and says so.
const SLICES = 8; // one per series colour
const TILES = 60;
const BARS = 60;

/** The result with all but the largest `keep - 1` parts summed into one
    "Other" row, or unchanged if it already fits. */
function grouped(run: Run, labelName: string, valueName: string, keep: number) {
  if (run.rows.length <= keep) return { run, rest: [] as string[], note: undefined };
  const label = run.columns.findIndex((c) => c.name === labelName);
  const value = run.columns.findIndex((c) => c.name === valueName);
  const size = (row: Cell[]) => Number(row[value] ?? 0);
  const kept = new Set([...run.rows].sort((a, b) => size(b) - size(a)).slice(0, keep - 1));
  const others = run.rows.filter((row) => !kept.has(row));
  const other = run.columns.map((): Cell => null);
  other[label] = `Other (${others.length})`;
  other[value] = others.reduce((sum, row) => sum + size(row), 0);
  return {
    run: { ...run, rows: [...run.rows.filter((row) => kept.has(row)), other] },
    rest: others.map((row) => String(row[label])),
    note: `${keep - 1} largest shown, ${others.length} more grouped as Other`,
  };
}
type Plotted = Exclude<Spec, { kind: "stat" | "table" }>;

export const config: Partial<Config> = { displayModeBar: false, responsive: true };

function theme() {
  const color = (name: string) => token(`color-${name}`);
  return {
    bg: color("bg"),
    surface: color("surface"),
    raised: color("raised"),
    line: color("line"),
    lineStrong: color("line-strong"),
    ink: color("ink"),
    inkStrong: color("ink-strong"),
    inkMuted: color("ink-muted"),
    inkFaint: color("ink-faint"),
    series: Array.from({ length: 8 }, (_, i) => color(`series-${i + 1}`)),
    font: token("font-sans"),
    // One hue for magnitude: low values recede toward the dark surface, but
    // stay visible against it.
    ramp: [
      [0, "#274472"],
      [0.5, "#3b82f6"],
      [1, "#c7ddff"],
    ] as [number, string][],
  };
}
type Theme = ReturnType<typeof theme>;

/** Reads the result by column name. */
function reader(run: Run) {
  const at = (name: string) => run.columns.findIndex((c) => c.name === name);
  return {
    column: (name: string) => run.columns[at(name)] as Column,
    values: (name: string): Cell[] => run.rows.map((row) => row[at(name)] ?? null),
  };
}

/** Values as Plotly should place them: dates and numbers as they are (whole
    numbers on a category axis as text, so years are not spaced like amounts),
    everything else as display text. */
function placed(values: Cell[], column: Column, categorical: boolean): (string | number | null)[] {
  if (column.kind === "time") return values as string[];
  if (column.kind === "number" && !categorical) return values as number[];
  return values.map((v) => cell(v, column, true));
}

function distinct<T>(values: T[]): T[] {
  return [...new Set(values)];
}

function axis(t: Theme, extra: object = {}) {
  return {
    gridcolor: t.line,
    linecolor: t.lineStrong,
    zerolinecolor: t.lineStrong,
    tickfont: { color: t.inkFaint, size: 11 },
    title: { font: { color: t.inkMuted, size: 12 } },
    automargin: true,
    ...extra,
  };
}

function base(t: Theme, traces: number): Partial<Layout> {
  return {
    paper_bgcolor: t.bg,
    plot_bgcolor: t.bg,
    font: { family: t.font, color: t.inkMuted, size: 12 },
    colorway: t.series,
    margin: { l: 56, r: 24, t: traces > 1 ? 36 : 12, b: 44 },
    showlegend: traces > 1,
    legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: t.ink } },
    hoverlabel: {
      bgcolor: t.raised,
      bordercolor: t.lineStrong,
      font: { family: t.font, color: t.inkStrong, size: 12 },
    },
    xaxis: axis(t),
    yaxis: axis(t),
  };
}

/** One group of rows per series value, in first-seen order. */
function split(run: Run, by: string | null | undefined) {
  if (!by) return [{ name: "", rows: run.rows.map((_, i) => i) }];
  const r = reader(run);
  const names = r.values(by).map((v) => cell(v, r.column(by), true));
  return distinct(names).map((name) => ({
    name,
    rows: names.flatMap((n, i) => (n === name ? [i] : [])),
  }));
}

function xy(spec: Extract<Plotted, { kind: "bar" | "stacked_bar" | "line" | "area" }>, run: Run, t: Theme): Figure {
  const isBar = spec.kind === "bar" || spec.kind === "stacked_bar";
  const categorical = isBar && reader(run).column(spec.x).kind !== "time";
  let note: string | undefined;
  if (categorical) {
    const all = distinct(reader(run).values(spec.x));
    if (all.length > BARS) {
      const first = new Set(all.slice(0, BARS));
      const at = run.columns.findIndex((c) => c.name === spec.x);
      run = { ...run, rows: run.rows.filter((row) => first.has(row[at] ?? null)) };
      note = `First ${BARS} of ${all.length} shown`;
    }
  }
  const r = reader(run);
  const x = r.column(spec.x);
  const horizontal = isBar && (x.kind === "text" || x.kind === "boolean");
  const xs = placed(r.values(spec.x), x, categorical);
  const ys = r.values(spec.y) as (number | null)[];
  const groups = split(run, spec.series);
  const measure = label(spec.y);

  const data = groups.map((group, i): Data => {
    const gx = group.rows.map((row) => xs[row] ?? null);
    const gy = group.rows.map((row) => ys[row] ?? null);
    const color = t.series[i % t.series.length] as string;
    const name = group.name || measure;
    if (isBar) {
      return {
        type: "bar",
        name,
        orientation: horizontal ? "h" : "v",
        x: horizontal ? gy : gx,
        y: horizontal ? gx : gy,
        marker: { color, cornerradius: 4 } as object,
        hovertemplate: horizontal ? `%{y}<br><b>%{x:,}</b> ${name}<extra></extra>` : undefined,
      };
    }
    const filled = spec.kind === "area";
    return {
      type: "scatter",
      mode: "lines",
      name,
      x: gx,
      y: gy,
      line: { color, width: 2 },
      stackgroup: filled && groups.length > 1 ? "one" : undefined,
      fill: filled ? (groups.length > 1 ? "tonexty" : "tozeroy") : undefined,
      fillcolor: filled ? `${color}${groups.length > 1 ? "99" : "26"}` : undefined,
    };
  });

  const layout = base(t, groups.length);
  const wholeNumbers = x.kind === "number" && r.values(spec.x).every((v) => Number.isInteger(v));
  if (horizontal) {
    layout.yaxis = axis(t, { autorange: "reversed", showgrid: false, tickfont: { color: t.ink, size: 12 } });
    layout.xaxis = axis(t, { rangemode: "tozero" });
    layout.margin = { ...layout.margin, l: 8 };
    layout.hovermode = "closest";
  } else {
    layout.xaxis = axis(t, {
      showgrid: false,
      type: categorical ? "category" : undefined,
      tickformat: wholeNumbers ? "d" : undefined,
    });
    layout.yaxis = axis(t, { rangemode: "tozero" });
    layout.hovermode = "x unified";
  }
  layout.barmode = spec.kind === "stacked_bar" ? "stack" : "group";
  layout.bargap = 0.3;
  return { data, layout, note };
}

function parts(spec: Extract<Plotted, { kind: "donut" | "treemap" }>, whole: Run, t: Theme): Figure {
  const { run, rest, note } = grouped(whole, spec.label, spec.value, spec.kind === "donut" ? SLICES : TILES);
  const r = reader(run);
  // Hovering "Other" lists what is in it.
  const inside = rest.length ? `<br>${rest.slice(0, 12).join(", ")}${rest.length > 12 ? ", …" : ""}` : "";
  const extra = run.rows.map((_, i) => (rest.length && i === run.rows.length - 1 ? inside : ""));
  const colors = run.rows.map((_, i) => (extra[i] ? t.inkFaint : (t.series[i % t.series.length] as string)));
  const labels = r.values(spec.label).map((v) => cell(v, r.column(spec.label), true));
  const values = r.values(spec.value) as number[];
  const layout = { ...base(t, 1), margin: { l: 16, r: 16, t: 16, b: 16 } };
  if (spec.kind === "donut") {
    // Thin slices are named in the legend only, so their labels cannot collide.
    const total = values.reduce((sum, v) => sum + v, 0) || 1;
    const text = values.map((v, i) => (v / total >= 0.04 ? `${labels[i]}<br>${((100 * v) / total).toFixed(1)}%` : ""));
    layout.showlegend = true;
    layout.legend = { ...layout.legend, orientation: "v", x: 1, xanchor: "right", y: 1, yanchor: "top" };
    return {
      layout,
      note,
      data: [
        {
          type: "pie",
          customdata: extra,
          hole: 0.62,
          labels,
          values,
          sort: false,
          direction: "clockwise",
          marker: { colors, line: { color: t.bg, width: 2 } },
          text,
          textinfo: "text",
          textposition: "outside",
          textfont: { color: t.ink },
          hovertemplate: `%{label}<br><b>%{value:,}</b> ${label(spec.value)} (%{percent})%{customdata}<extra></extra>`,
        },
      ],
    };
  }
  return {
    layout,
    note,
    data: [
      {
        type: "treemap",
        customdata: extra,
        labels,
        values,
        parents: labels.map(() => ""),
        branchvalues: "total",
        marker: { colors: values, colorscale: t.ramp, line: { color: t.bg, width: 2 } } as object,
        textinfo: "label+value+percent root",
        textfont: { color: "#ffffff" },
        hovertemplate: `%{label}<br><b>%{value:,}</b> ${label(spec.value)}%{customdata}<extra></extra>`,
        tiling: { pad: 2 },
      } as unknown as Data, // the type definitions lag behind treemap's options
    ],
  };
}

function heatmap(spec: Extract<Plotted, { kind: "heatmap" }>, run: Run, t: Theme): Figure {
  const r = reader(run);
  const xs = placed(r.values(spec.x), r.column(spec.x), true);
  const ys = placed(r.values(spec.y), r.column(spec.y), true);
  const values = r.values(spec.value) as (number | null)[];
  const [columns, rows] = [distinct(xs), distinct(ys)];
  const z = rows.map(() => columns.map((): number | null => null));
  xs.forEach((x, i) => {
    const line = z[rows.indexOf(ys[i] ?? null)];
    if (line) line[columns.indexOf(x)] = values[i] ?? null;
  });
  const layout = base(t, 1);
  const side = { showgrid: false, type: r.column(spec.x).kind === "time" ? undefined : "category" };
  layout.xaxis = axis(t, side);
  layout.yaxis = axis(t, { showgrid: false, type: "category", autorange: "reversed", tickfont: { color: t.ink, size: 12 } });
  layout.margin = { ...layout.margin, l: 8 };
  return {
    layout,
    data: [
      {
        type: "heatmap",
        x: columns,
        y: rows,
        z,
        colorscale: t.ramp,
        xgap: 2,
        ygap: 2,
        hoverongaps: false,
        colorbar: { thickness: 10, outlinewidth: 0, tickfont: { color: t.inkFaint, size: 11 } },
        hovertemplate: `%{y} · %{x}<br><b>%{z:,}</b> ${label(spec.value)}<extra></extra>`,
      } as Data,
    ],
  };
}

function scatter(spec: Extract<Plotted, { kind: "scatter" | "scatter3d" }>, run: Run, t: Theme): Figure {
  const r = reader(run);
  const three = spec.kind === "scatter3d" && !!spec.z;
  const names = spec.label ? r.values(spec.label).map((v) => cell(v, r.column(spec.label as string), true)) : undefined;
  const numeric = spec.color ? r.column(spec.color).kind === "number" : false;
  const groups = split(run, spec.color && !numeric ? spec.color : null);
  const pick = <T,>(values: T[], rows: number[]) => rows.map((row) => values[row] as T);
  const [xs, ys] = [r.values(spec.x), r.values(spec.y)];
  const zs = three ? r.values(spec.z as string) : [];
  const shade = numeric ? (r.values(spec.color as string) as number[]) : [];

  const data = groups.map((group, i): Data => {
    const marker = {
      size: three ? 4 : 8,
      opacity: 0.85,
      line: { color: t.bg, width: three ? 0 : 1 },
      ...(numeric
        ? {
            color: pick(shade, group.rows),
            colorscale: t.ramp,
            showscale: true,
            colorbar: {
              thickness: 10,
              outlinewidth: 0,
              title: { text: label(spec.color as string), font: { color: t.inkMuted, size: 11 } },
              tickfont: { color: t.inkFaint, size: 11 },
            },
          }
        : { color: t.series[i % t.series.length] }),
    };
    return {
      type: three ? "scatter3d" : "scatter",
      mode: "markers",
      name: group.name || label(spec.y),
      x: pick(xs, group.rows),
      y: pick(ys, group.rows),
      ...(three ? { z: pick(zs, group.rows) } : {}),
      text: names ? pick(names, group.rows) : undefined,
      marker,
    } as Data;
  });

  const layout = base(t, groups.length);
  const titled = (name: string) => axis(t, { title: { text: label(name), font: { color: t.inkMuted, size: 12 } } });
  if (three) {
    const side = (name: string) => ({ ...titled(name), backgroundcolor: t.bg, showbackground: true });
    layout.scene = {
      xaxis: side(spec.x),
      yaxis: side(spec.y),
      zaxis: side(spec.z as string),
      bgcolor: t.bg,
    } as Layout["scene"];
    layout.margin = { l: 0, r: 0, t: groups.length > 1 ? 36 : 0, b: 0 };
  } else {
    layout.xaxis = titled(spec.x);
    layout.yaxis = titled(spec.y);
    layout.hovermode = "closest";
  }
  return { data, layout };
}

export function figure(spec: Plotted, run: Run): Figure {
  const t = theme();
  switch (spec.kind) {
    case "bar":
    case "stacked_bar":
    case "line":
    case "area":
      return xy(spec, run, t);
    case "donut":
    case "treemap":
      return parts(spec, run, t);
    case "heatmap":
      return heatmap(spec, run, t);
    case "scatter":
    case "scatter3d":
      return scatter(spec, run, t);
  }
}

/** The figure as it should look in an exported image: titled, on its own. */
export function titled(fig: Figure, title: string, subtitle: string): Figure {
  const t = theme();
  const margin = fig.layout.margin ?? {};
  if (fig.note) subtitle = `${subtitle} · ${fig.note}`;
  return {
    data: fig.data,
    layout: {
      ...fig.layout,
      title: {
        text: `${title}<br><span style="font-size:13px;color:${t.inkMuted}">${subtitle}</span>`,
        x: 0.02,
        xanchor: "left",
        font: { color: t.inkStrong, size: 20 },
      },
      margin: { ...margin, t: (margin.t ?? 0) + 84, l: Math.max(margin.l ?? 0, 32), r: 32, b: Math.max(margin.b ?? 0, 48) },
    },
  };
}
