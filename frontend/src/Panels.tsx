import { ArrowUp, ChevronDown, Code2, Download, Keyboard, Mic, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Plus, Settings as SettingsIcon, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Chart, Chat, FeedEvent, Kind, Narrow, Run, Source } from "./api";
import { ChartBody, figure } from "./charts/ChartBody";
import { date, number, slug } from "./charts/format";
import { seriesOf, titled } from "./charts/plot";
import { short } from "./Settings";
import { IconButton, Menu } from "./ui";
import { VoiceBar } from "./Voice";

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
  onSettings: () => void;
  /** Shown as a narrow rail, to give the charts more room. */
  collapsed: boolean;
  onCollapse: (collapsed: boolean) => void;
}) {
  const { chats, active, source } = props;
  if (props.collapsed)
    return (
      <aside className="flex min-h-0 flex-col items-center gap-1 border-r border-line bg-surface py-3">
        <IconButton label="Show chats" onClick={() => props.onCollapse(false)}>
          <PanelLeftOpen size={16} />
        </IconButton>
        <IconButton label="New chat" onClick={props.onNew}>
          <Plus size={16} />
        </IconButton>
        <span className="flex-1" />
        <IconButton label="Settings" onClick={props.onSettings}>
          <SettingsIcon size={16} />
        </IconButton>
      </aside>
    );
  return (
    <aside className="flex min-h-0 flex-col border-r border-line bg-surface">
      <div className="flex items-center gap-2 py-3 pr-2 pl-4">
        <Wave size={22} />
        <span className="flex-1 text-lg font-bold tracking-tight">
          <span className="text-ink">talk</span>
          <span className="text-primary">viz</span>
        </span>
        <IconButton label="Hide chats" onClick={() => props.onCollapse(true)}>
          <PanelLeftClose size={16} />
        </IconButton>
      </div>
      <div className="px-3 pb-3">
        <button
          type="button"
          onClick={props.onNew}
          className="flex w-full cursor-pointer items-center gap-2 rounded-md border border-line-strong px-2.5 py-1.5 text-left text-sm text-ink hover:border-ink-faint hover:text-ink-strong"
        >
          <Plus size={15} aria-hidden />
          New chat
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
      <div className="flex items-end justify-between gap-2 border-t border-line px-4 py-3 text-xs text-ink-faint">
        <div className="min-w-0">
          {source && (
            <>
              <div className="truncate text-ink-muted" title={source.description}>
                {source.title}
              </div>
              {source.data_version && <div>Data as of {source.data_version}</div>}
            </>
          )}
        </div>
        <IconButton label="Settings" onClick={props.onSettings}>
          <SettingsIcon size={16} />
        </IconButton>
      </div>
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
function palette(all: Chart[], chart: Chart): string[] | undefined {
  const parent = all.find((c) => c.id === chart.parent);
  return parent?.run ? seriesOf(parent.spec, parent.run) : undefined;
}

export function Workspace(props: {
  charts: Chart[];
  /** Every chart in the chat, shown or not. */
  all: Chart[];
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
          palette={palette(props.all, chart)}
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
  /** Its parent's series, for a sub-chart, so the same series has the same colour. */
  palette: string[] | undefined;
  onDraw: (chartId: string, kind: Kind) => void;
  onReset: (chartId: string) => void;
  onClose: (chartId: string) => void;
}) {
  const { chart, source, beside } = props;
  const [details, setDetails] = useState(false);
  const kind = chart.view ?? chart.spec.kind;
  const run = chart.run;
  const spec = chart.views.find((view) => view.kind === kind) ?? chart.spec;
  const fig = useMemo(
    () => (run && spec.kind !== "stat" && spec.kind !== "table" ? figure(spec, run, chart.lens, props.palette) : null),
    // The palette is a new array each render; its contents are what matter.
    [spec, run, chart.lens, props.palette?.join("\n")],
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

  return (
    <section className={`flex min-h-0 min-w-0 flex-col bg-bg ${beside ? "p-3" : "px-5 pt-4 pb-3"} ${props.wide ? "col-span-2" : ""}`}>
      <header className={`flex items-start gap-1.5 ${beside ? "mb-2" : "mb-3"}`}>
        <div className="min-w-0 flex-1">
          <h1 className={`truncate font-semibold text-ink-strong ${beside ? "text-sm" : "text-base"}`}>{chart.title}</h1>
          <p className="truncate text-xs text-ink-muted">
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
        {chart.views.length > 1 && (
          <Menu
            name="View"
            label={
              <>
                {KINDS[spec.kind]}
                <ChevronDown size={13} aria-hidden />
              </>
            }
            items={chart.views.map((view) => ({
              key: view.kind,
              label: KINDS[view.kind],
              checked: view.kind === spec.kind,
              onSelect: () => props.onDraw(chart.id, view.kind),
            }))}
          />
        )}
        <Menu
          name="Download"
          label={<Download size={13} aria-hidden />}
          items={[
            ...(fig ? [{ key: "png", label: "Image (PNG)", onSelect: () => void saveImage() }] : []),
            { key: "csv", label: "Data (CSV)", onSelect: saveData },
          ]}
        />
        <IconButton label={details ? "Hide the query" : "Show the query behind this chart"} pressed={details} onClick={() => setDetails((open) => !open)}>
          <Code2 size={15} />
        </IconButton>
        {beside && (
          <IconButton label={`Close ${chart.title}`} onClick={() => props.onClose(chart.id)}>
            <X size={15} />
          </IconButton>
        )}
      </header>
      <div className={`relative min-h-0 min-w-0 flex-1 transition-opacity ${props.busy ? "opacity-50" : ""}`}>
        <div className="absolute inset-0 overflow-auto">
          <ChartBody spec={spec} run={run} fig={fig} />
        </div>
      </div>
      {details && (
        <div className="mt-3 max-h-56 overflow-auto rounded-md border border-line bg-surface p-3">
          {chart.parent && (
            <p className="mb-2 border-b border-line pb-2 text-xs text-ink-muted">
              {chart.narrow
                ? `Rows kept from chart ${chart.parent.slice(1)}'s result: ${kept(chart.narrow)}. Its query:`
                : `Made from chart ${chart.parent.slice(1)} with a query of its own:`}
            </p>
          )}
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

const OPS: Record<string, string> = { create_chart: "Created", update_chart: "Updated", show_chart: "Showing", set_view: "Redrawn", set_lens: "Adjusted", narrow_chart: "Filtered", discuss: "About" };

export function Feed(props: {
  events: FeedEvent[];
  pending: string | null;
  error: string | null;
  /** What the chat runs on, in a line; opens the settings when clicked. */
  running: string | null;
  /** Typing, or talking with the space bar held. */
  mode: "chat" | "voice";
  chatId: string | null;
  onMode: (mode: "chat" | "voice") => void;
  onInterrupt: () => void;
  onError: (message: string) => void;
  onSay: (text: string) => void;
  onFocus: (chartId: string) => void;
  onSettings: () => void;
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
    <section className="flex min-h-0 min-w-0 flex-col bg-surface" aria-label="Conversation">
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
            <Line key={event.id} who={event.kind} text={event.text ?? ""} flagged={event.kind === "assistant" && event.verified === false ? unverified(event) : null} made={event.kind === "assistant" ? made(event) : null} />
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
        className="m-3 mt-0 rounded-xl border border-line-strong bg-bg focus-within:border-primary"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {props.mode === "voice" ? (
          <VoiceBar chatId={props.chatId} busy={busy} onInterrupt={props.onInterrupt} onSay={props.onSay} onError={props.onError} />
        ) : (
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Ask about your data…"
            aria-label="Your question"
            autoFocus
            className="block h-11 w-full bg-transparent px-3.5 text-ink-strong outline-none placeholder:text-ink-faint focus-visible:outline-none"
          />
        )}
        <div className="flex items-center gap-2 px-2 pb-2">
          <div role="group" aria-label="How to ask" className="flex shrink-0 rounded-md bg-surface p-0.5 text-xs">
            {(["chat", "voice"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                aria-pressed={props.mode === mode}
                onClick={() => props.onMode(mode)}
                className={`flex cursor-pointer items-center gap-1.5 rounded px-2 py-1 capitalize ${props.mode === mode ? "bg-raised text-ink-strong" : "text-ink-muted hover:text-ink"}`}
              >
                {mode === "chat" ? <Keyboard size={13} aria-hidden /> : <Mic size={13} aria-hidden />}
                {mode}
              </button>
            ))}
          </div>
          {props.running && (
            <button
              type="button"
              onClick={props.onSettings}
              title="Change the models and voice for this chat"
              className="flex min-w-0 cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-xs text-ink-muted hover:bg-surface hover:text-ink-strong"
            >
              <SlidersHorizontal size={13} className="shrink-0" aria-hidden />
              <span className="truncate">{props.running}</span>
            </button>
          )}
          <span className="flex-1" />
          {props.mode === "chat" && (
            <button
              type="submit"
              disabled={busy || !text.trim()}
              aria-label="Ask"
              title="Ask"
              className="flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-md bg-primary text-white hover:bg-primary-bright disabled:cursor-default disabled:bg-raised disabled:text-ink-faint"
            >
              <ArrowUp size={16} />
            </button>
          )}
        </div>
      </form>
    </section>
  );
}

/** How a reply was produced: "claude-sonnet-5.5 → claude-haiku-5.5 · 4.1 s", or "fast path · 0.3 s". */
function made(event: FeedEvent): string | null {
  if (!event.models || !event.seconds) return null;
  const { plan, reply } = event.models;
  const who = [plan, reply].filter((m): m is string => Boolean(m)).map(short);
  const total = Object.values(event.seconds).reduce((sum, s) => sum + s, 0);
  const how = plan ? who.join(" → ") : ["fast path", ...who].join(" → ");
  return `${how} · ${total.toFixed(1)} s`;
}

function Line({ who, text, flagged = null, made = null }: { who: "user" | "assistant"; text: string; flagged?: string | null; made?: string | null }) {
  return (
    <div className="flex gap-3">
      <span className={`w-5 shrink-0 pt-0.5 text-xs font-medium ${who === "user" ? "text-ink-faint" : "text-primary-bright"}`}>
        {who === "user" ? "You" : <Wave size={18} />}
      </span>
      <p className={`max-w-3xl leading-relaxed ${who === "user" ? "text-ink-muted" : "text-ink-strong"}`} title={made ?? undefined}>
        {text}
        {flagged && (
          <span className="ml-2 cursor-help text-xs text-warning underline decoration-dotted underline-offset-2" title={flagged} tabIndex={0}>
            unverified
          </span>
        )}
        {made && <span className="mt-0.5 block text-xs text-ink-faint">{made}</span>}
      </p>
    </div>
  );
}

/** A narrowing in words: "make is FORD or KIA; year from 2022; top 5 by complaints". */
function kept(narrow: Narrow): string {
  const parts = narrow.where.map((c) => {
    if (c.values) return `${c.column} is ${c.values.join(" or ")}`;
    if (c.min != null && c.max != null) return `${c.column} from ${c.min} to ${c.max}`;
    return c.min != null ? `${c.column} from ${c.min}` : `${c.column} up to ${c.max}`;
  });
  if (narrow.top) parts.push(`top ${narrow.top} by ${narrow.by}`);
  return parts.join("; ");
}

export function ChartList(props: {
  charts: Chart[];
  shown: string[];
  /** No room to show another chart beside those showing. */
  full: boolean;
  onFocus: (id: string) => void;
  onToggle: (id: string) => void;
  collapsed: boolean;
  onCollapse: (collapsed: boolean) => void;
}) {
  if (props.collapsed)
    return (
      <aside className="flex min-h-0 flex-col items-center gap-1 border-l border-line bg-surface py-3">
        <IconButton label="Show the chart list" onClick={() => props.onCollapse(false)}>
          <PanelRightOpen size={16} />
        </IconButton>
        {props.charts.length > 0 && <span className="text-xs text-ink-muted tabular-nums">{props.charts.length}</span>}
      </aside>
    );
  return (
    <aside className="flex min-h-0 flex-col border-l border-line bg-surface">
      <div className="flex items-center py-3 pr-2 pl-4">
        <h2 className="flex-1 text-xs font-medium tracking-wide text-ink-muted uppercase">Charts</h2>
        <IconButton label="Hide the chart list" onClick={() => props.onCollapse(true)}>
          <PanelRightClose size={16} />
        </IconButton>
      </div>
      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-2" aria-label="Charts in this chat">
        {props.charts.length === 0 && <p className="px-2.5 text-sm text-ink-faint">Charts you create appear here.</p>}
        {props.charts.map((chart) => {
          const on = props.shown.includes(chart.id);
          const parent = props.charts.find((c) => c.id === chart.parent);
          const stale = parent && chart.based_on !== null && parent.revision > chart.based_on;
          // The only chart showing cannot be taken away, and a full workspace takes no more.
          const can = on ? props.shown.length > 1 : props.shown.length > 0 && !props.full;
          return (
            <div
              key={chart.id}
              className={`group flex items-start rounded-md ${chart.parent ? "ml-5" : ""} ${on ? "bg-raised text-ink-strong" : "text-ink-muted hover:bg-raised/50 hover:text-ink"}`}
            >
              <button
                type="button"
                onClick={() => props.onFocus(chart.id)}
                aria-current={on ? "true" : undefined}
                className="flex min-w-0 flex-1 cursor-pointer gap-2.5 px-2.5 py-2 text-left"
              >
                <span className="w-4 shrink-0 text-xs leading-5 text-ink-faint tabular-nums">{chart.id.slice(1)}</span>
                <span className="min-w-0">
                  <span className="block text-sm leading-5">{chart.title}</span>
                  <span className="block text-xs text-ink-faint">
                    {KINDS[chart.view ?? chart.spec.kind]}
                    {stale && <span title={`Chart ${parent.id.slice(1)} has changed since this was made from it.`}> · may be out of date</span>}
                  </span>
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
