import { useEffect, useMemo, useRef, useState } from "react";
import type { Chart, Chat, FeedEvent, Kind, Run, Source } from "./api";
import { ChartBody, figure } from "./charts/ChartBody";
import { date, number, slug } from "./charts/format";
import { titled } from "./charts/plot";

const BARS = [
  [51, 112, 32],
  [89, 83, 90],
  [127, 57, 142],
  [165, 85, 86],
  [203, 111, 34],
] as const;

/** The waveform mark; `live` animates it while the agent is working. */
export function Wave({ size = 20, live = false }: { size?: number; live?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="30 40 194 176" aria-hidden className="shrink-0">
      <g fill="none" stroke="var(--color-primary)" strokeWidth={15} strokeLinecap="round">
        {BARS.map(([x, y, length], i) => (
          <path
            key={x}
            d={`M${x} ${y}v${length}`}
            className={live ? "animate-wave" : undefined}
            style={{ transformOrigin: "center", transformBox: "fill-box", animationDelay: `${i * 0.12}s` }}
          />
        ))}
      </g>
    </svg>
  );
}

export function Sidebar(props: {
  chats: Chat[];
  active: string | null;
  source: Source | undefined;
  onOpen: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  const { chats, active, source } = props;
  return (
    <aside className="flex min-h-0 flex-col border-r border-line bg-surface">
      <div className="flex items-center gap-2 px-4 pt-4 pb-3">
        <Wave size={26} />
        <span className="text-xl font-bold tracking-tight">
          <span className="text-ink">talk</span>
          <span className="text-primary">viz</span>
        </span>
      </div>
      <div className="px-3 pb-3">
        <button
          type="button"
          onClick={props.onNew}
          className="w-full cursor-pointer rounded-md border border-line-strong px-3 py-2 text-left text-sm text-ink hover:border-primary hover:text-ink-strong"
        >
          + New chat
        </button>
      </div>
      <nav className="min-h-0 flex-1 overflow-y-auto px-2" aria-label="Chats">
        {chats.map((chat) => (
          <div
            key={chat.id}
            className={`group flex items-center rounded-md ${chat.id === active ? "bg-raised text-ink-strong" : "text-ink-muted hover:bg-raised/50 hover:text-ink"}`}
          >
            <button
              type="button"
              onClick={() => props.onOpen(chat.id)}
              aria-current={chat.id === active ? "page" : undefined}
              className="min-w-0 flex-1 cursor-pointer truncate px-2.5 py-2 text-left text-sm"
            >
              {chat.title}
            </button>
            <button
              type="button"
              aria-label={`Delete chat: ${chat.title}`}
              onClick={() => props.onDelete(chat.id)}
              className="cursor-pointer px-2 text-ink-faint opacity-0 group-hover:opacity-100 hover:text-critical focus-visible:opacity-100"
            >
              ×
            </button>
          </div>
        ))}
      </nav>
      {source && (
        <div className="border-t border-line px-4 py-3 text-xs text-ink-faint">
          <div className="truncate text-ink-muted" title={source.description}>
            {source.title}
          </div>
          {source.data_version && <div>Data as of {source.data_version}</div>}
        </div>
      )}
    </aside>
  );
}

const KINDS: Record<Kind, string> = {
  stat: "Number",
  table: "Table",
  bar: "Bars",
  stacked_bar: "Stacked bars",
  line: "Line",
  area: "Area",
  donut: "Donut",
  treemap: "Treemap",
  heatmap: "Heatmap",
  scatter: "Scatter",
  scatter3d: "3D scatter",
};

function download(name: string, href: string) {
  const link = document.createElement("a");
  link.href = href;
  link.download = name;
  link.click();
}

function csv(run: Run): string {
  const quote = (value: unknown) => {
    const text = value === null ? "" : String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [run.columns.map((c) => c.name), ...run.rows].map((row) => row.map(quote).join(",")).join("\n");
}

function Empty(props: { source: Source | undefined; busy: boolean; onAsk: (text: string) => void }) {
  const { source } = props;
  return (
    <section className="flex min-h-0 min-w-0 flex-col items-center justify-center gap-5 p-8 text-center">
      <Wave size={56} live={props.busy} />
      <div>
        <h1 className="text-2xl font-semibold text-ink-strong">Talk to your data.</h1>
        <p className="mt-1 text-ink-muted">
          {source ? `Ask a question about ${source.title}.` : "Register a data source with `talkviz add` to begin."}
        </p>
      </div>
      {source && (
        <ul className="flex max-w-2xl flex-wrap justify-center gap-2">
          {source.examples.map((question) => (
            <li key={question}>
              <button
                type="button"
                onClick={() => props.onAsk(question)}
                className="cursor-pointer rounded-full border border-line-strong px-3.5 py-1.5 text-sm text-ink hover:border-primary hover:text-ink-strong"
              >
                {question}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The charts on screen: one, or up to four side by side. */
export function Workspace(props: {
  charts: Chart[];
  source: Source | undefined;
  busy: boolean;
  onAsk: (text: string) => void;
  onDraw: (chartId: string, kind: Kind) => void;
  onReset: (chartId: string) => void;
  onClose: (chartId: string) => void;
}) {
  const count = props.charts.length;
  if (count === 0) return <Empty source={props.source} busy={props.busy} onAsk={props.onAsk} />;
  const columns = count > 1 ? "grid-cols-[repeat(2,minmax(0,1fr))]" : "grid-cols-[minmax(0,1fr)]";
  const rows = count > 2 ? "grid-rows-[repeat(2,minmax(0,1fr))]" : "grid-rows-[minmax(0,1fr)]";
  return (
    <div className={`grid min-h-0 min-w-0 gap-px bg-line ${columns} ${rows}`}>
      {props.charts.map((chart, i) => (
        <Pane
          key={chart.id}
          chart={chart}
          source={props.source}
          busy={props.busy}
          beside={count > 1}
          wide={count === 3 && i === 2}
          onDraw={props.onDraw}
          onReset={props.onReset}
          onClose={props.onClose}
        />
      ))}
    </div>
  );
}

function Pane(props: {
  chart: Chart;
  source: Source | undefined;
  busy: boolean;
  /** Shown beside other charts, so its controls are kept small. */
  beside: boolean;
  wide: boolean;
  onDraw: (chartId: string, kind: Kind) => void;
  onReset: (chartId: string) => void;
  onClose: (chartId: string) => void;
}) {
  const { chart, source, beside } = props;
  const [details, setDetails] = useState(false);
  const [saving, setSaving] = useState(false);
  const kind = chart.view ?? chart.spec.kind;
  const run = chart.run;
  const spec = chart.views.find((view) => view.kind === kind) ?? chart.spec;
  const fig = useMemo(
    () => (run && spec.kind !== "stat" && spec.kind !== "table" ? figure(spec, run, chart.lens) : null),
    [spec, run, chart.lens],
  );

  const subtitle = run ? `${source?.title ?? chart.title} · data as of ${run.data_version}` : "";
  const saveImage = async () => {
    if (!fig) return;
    const Plotly = await import("plotly.js-dist-min");
    const image = await Plotly.toImage(titled(fig, chart.title, subtitle) as Parameters<typeof Plotly.toImage>[0], {
      format: "png",
      width: 1400,
      height: 800,
      scale: 2,
    });
    download(`${slug(chart.title)}.png`, image);
  };
  const saveData = () => {
    if (!run) return;
    const url = URL.createObjectURL(new Blob([csv(run)], { type: "text/csv" }));
    download(`${slug(chart.title)}.csv`, url);
    URL.revokeObjectURL(url);
  };
  const quiet = "cursor-pointer rounded-md border px-2.5 py-1.5 text-xs";

  return (
    <section className={`flex min-h-0 min-w-0 flex-col bg-bg ${beside ? "p-3" : "p-5 pb-3"} ${props.wide ? "col-span-2" : ""}`}>
      <header className={`flex flex-wrap items-start ${beside ? "mb-2 gap-2" : "mb-4 gap-3"}`}>
        <div className="min-w-0 flex-1">
          <h1 className={`truncate font-semibold text-ink-strong ${beside ? "text-sm" : "text-lg"}`}>{chart.title}</h1>
          <p className="text-xs text-ink-faint">
            {KINDS[spec.kind]}
            {run && ` · ${number(run.rows.length)} ${run.rows.length === 1 ? "row" : "rows"} · data as of ${run.data_version}`}
            {chart.revision > 1 && ` · revision ${chart.revision}`}
            {fig?.note && ` · ${fig.note}`}
            {chart.lens && fig?.note && (
              <button type="button" onClick={() => props.onReset(chart.id)} className="ml-2 cursor-pointer text-primary-bright hover:underline">
                Reset
              </button>
            )}
          </p>
        </div>
        {chart.views.length > 1 && beside && (
          <select
            aria-label="View"
            value={spec.kind}
            onChange={(event) => props.onDraw(chart.id, event.target.value as Kind)}
            className="cursor-pointer rounded-md border border-line-strong bg-bg px-2 py-1.5 text-xs text-ink"
          >
            {chart.views.map((view) => (
              <option key={view.kind} value={view.kind}>
                {KINDS[view.kind]}
              </option>
            ))}
          </select>
        )}
        {chart.views.length > 1 && !beside && (
          <div className="flex rounded-md border border-line-strong p-0.5 text-xs" role="group" aria-label="View">
            {chart.views.map((view) => (
              <button
                key={view.kind}
                type="button"
                aria-pressed={spec.kind === view.kind}
                onClick={() => props.onDraw(chart.id, view.kind)}
                className={`cursor-pointer rounded px-2.5 py-1 ${spec.kind === view.kind ? "bg-raised text-ink-strong" : "text-ink-muted hover:text-ink"}`}
              >
                {KINDS[view.kind]}
              </button>
            ))}
          </div>
        )}
        <div className="relative">
          <button
            type="button"
            aria-expanded={saving}
            aria-haspopup="menu"
            onClick={() => setSaving((open) => !open)}
            className={`${quiet} ${saving ? "border-primary text-ink-strong" : "border-line-strong text-ink-muted hover:text-ink"}`}
          >
            Download
          </button>
          {saving && (
            <div role="menu" className="absolute right-0 z-20 mt-1 w-44 rounded-md border border-line-strong bg-raised p-1 text-sm shadow-xl shadow-black/40">
              {fig && (
                <button
                  type="button"
                  role="menuitem"
                  className="block w-full cursor-pointer rounded px-2.5 py-1.5 text-left text-ink hover:bg-line-strong hover:text-ink-strong"
                  onClick={() => {
                    setSaving(false);
                    void saveImage();
                  }}
                >
                  Image (PNG)
                </button>
              )}
              <button
                type="button"
                role="menuitem"
                className="block w-full cursor-pointer rounded px-2.5 py-1.5 text-left text-ink hover:bg-line-strong hover:text-ink-strong"
                onClick={() => {
                  setSaving(false);
                  saveData();
                }}
              >
                Data (CSV)
              </button>
            </div>
          )}
        </div>
        <button
          type="button"
          aria-expanded={details}
          onClick={() => setDetails((open) => !open)}
          className={`${quiet} ${details ? "border-primary text-ink-strong" : "border-line-strong text-ink-muted hover:text-ink"}`}
        >
          {beside ? "Query" : "How this was computed"}
        </button>
        {beside && (
          <button
            type="button"
            aria-label={`Close ${chart.title}`}
            onClick={() => props.onClose(chart.id)}
            className={`${quiet} border-line-strong text-ink-muted hover:text-ink`}
          >
            ×
          </button>
        )}
      </header>
      <div className={`min-h-0 min-w-0 flex-1 overflow-auto transition-opacity ${props.busy ? "opacity-50" : ""}`}>
        <ChartBody spec={spec} run={run} fig={fig} />
      </div>
      {details && (
        <div className="mt-3 max-h-56 overflow-auto rounded-md border border-line bg-surface p-3">
          <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink">{chart.sql.trim()}</pre>
          {run && (
            <p className="mt-2 border-t border-line pt-2 text-xs text-ink-faint">
              Read-only query, run {date(run.ran_at)} in {run.seconds < 0.1 ? "under 0.1" : run.seconds.toFixed(1)} s on data as of{" "}
              {run.data_version}.{run.truncated && " The result was cut off at the row limit."}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

/** Why a reply carries the "unverified" tag. */
function unverified(event: FeedEvent): string {
  if (event.error) return "No answer could be worked out, so nothing here comes from the data.";
  const figures = [...new Set((event.rejected ?? []).flatMap((draft) => draft.unsupported))];
  const why = figures.length
    ? `The summary mentioned figures that are not in the result (${figures.join(", ")})`
    : "The summary could not be checked against the result";
  return `${why}, so it was left out. The chart and its numbers are unaffected.`;
}

const OPS: Record<string, string> = { create_chart: "Created", update_chart: "Updated", show_chart: "Showing", set_view: "Redrawn", set_lens: "Adjusted" };

export function Feed(props: {
  events: FeedEvent[];
  pending: string | null;
  error: string | null;
  onSay: (text: string) => void;
  onFocus: (chartId: string) => void;
}) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const busy = props.pending !== null;
  useEffect(() => {
    // Braces matter: scrollIntoView returns a promise in newer browsers, and
    // React would treat a returned value as the effect's cleanup.
    end.current?.scrollIntoView({ block: "end" });
  }, [props.events.length, props.pending]);
  const submit = () => {
    const said = text.trim();
    if (!said || busy) return;
    setText("");
    props.onSay(said);
  };
  return (
    <section className="flex min-h-0 min-w-0 flex-col border-t border-line bg-surface" aria-label="Conversation">
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-5 py-3" aria-live="polite">
        {props.events.map((event) =>
          event.kind === "chart" ? (
            <button
              key={event.id}
              type="button"
              onClick={() => event.chart_id && props.onFocus(event.chart_id)}
              className="ml-8 flex cursor-pointer items-center gap-1.5 rounded-full border border-line-strong px-2.5 py-0.5 text-xs text-ink-muted hover:border-primary hover:text-ink"
            >
              <span className="size-1.5 rounded-full bg-primary" />
              {OPS[event.op ?? ""] ?? "Chart"} · {event.title}
            </button>
          ) : (
            <Line key={event.id} who={event.kind} text={event.text ?? ""} flagged={event.kind === "assistant" && event.verified === false ? unverified(event) : null} />
          ),
        )}
        {props.pending && <Line who="user" text={props.pending} />}
        {busy && (
          <div className="flex items-center gap-3 text-ink-muted">
            <Wave size={20} live />
            <span className="text-sm">Working on it…</span>
          </div>
        )}
        {props.error && <p className="text-sm text-critical">Something went wrong: {props.error}</p>}
        <div ref={end} />
      </div>
      <form
        className="flex gap-2 border-t border-line p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask about your data…"
          aria-label="Your question"
          autoFocus
          className="min-w-0 flex-1 rounded-md border border-line-strong bg-bg px-3 py-2 text-ink-strong placeholder:text-ink-faint focus:border-primary focus:outline-none"
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="cursor-pointer rounded-md bg-primary px-4 py-2 font-medium text-white hover:bg-primary-bright disabled:cursor-default disabled:opacity-40"
        >
          Ask
        </button>
      </form>
    </section>
  );
}

function Line({ who, text, flagged = null }: { who: "user" | "assistant"; text: string; flagged?: string | null }) {
  return (
    <div className="flex gap-3">
      <span className={`w-5 shrink-0 pt-0.5 text-xs font-medium ${who === "user" ? "text-ink-faint" : "text-primary-bright"}`}>
        {who === "user" ? "You" : <Wave size={18} />}
      </span>
      <p className={`max-w-3xl leading-relaxed ${who === "user" ? "text-ink-muted" : "text-ink-strong"}`}>
        {text}
        {flagged && (
          <span className="ml-2 cursor-help text-xs text-warning underline decoration-dotted underline-offset-2" title={flagged} tabIndex={0}>
            unverified
          </span>
        )}
      </p>
    </div>
  );
}

export function ChartList(props: {
  charts: Chart[];
  shown: string[];
  /** No room to show another chart beside those showing. */
  full: boolean;
  onFocus: (id: string) => void;
  onToggle: (id: string) => void;
}) {
  return (
    <aside className="flex min-h-0 flex-col border-l border-line bg-surface">
      <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-ink-faint uppercase">Charts</h2>
      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-2" aria-label="Charts in this chat">
        {props.charts.length === 0 && <p className="px-2.5 text-sm text-ink-faint">Charts you create appear here.</p>}
        {props.charts.map((chart, i) => {
          const on = props.shown.includes(chart.id);
          // The only chart showing cannot be taken away, and a full workspace takes no more.
          const can = on ? props.shown.length > 1 : props.shown.length > 0 && !props.full;
          return (
            <div
              key={chart.id}
              className={`group flex items-start rounded-md ${on ? "bg-raised text-ink-strong" : "text-ink-muted hover:bg-raised/50 hover:text-ink"}`}
            >
              <button
                type="button"
                onClick={() => props.onFocus(chart.id)}
                aria-current={on ? "true" : undefined}
                className="flex min-w-0 flex-1 cursor-pointer gap-2.5 px-2.5 py-2 text-left"
              >
                <span className="w-4 shrink-0 text-xs leading-5 text-ink-faint tabular-nums">{i + 1}</span>
                <span className="min-w-0">
                  <span className="block text-sm leading-5">{chart.title}</span>
                  <span className="block text-xs text-ink-faint">{KINDS[chart.view ?? chart.spec.kind]}</span>
                </span>
              </button>
              {can && (
                <button
                  type="button"
                  onClick={() => props.onToggle(chart.id)}
                  aria-label={on ? `Stop showing ${chart.title}` : `Show ${chart.title} alongside`}
                  title={on ? "Stop showing" : "Show alongside"}
                  className="mt-1.5 mr-1.5 cursor-pointer rounded px-1.5 text-sm leading-6 text-ink-faint opacity-0 group-hover:opacity-100 hover:bg-line-strong hover:text-ink-strong focus-visible:opacity-100"
                >
                  {on ? "−" : "+"}
                </button>
              )}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
