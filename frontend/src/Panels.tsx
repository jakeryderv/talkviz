import { useEffect, useRef, useState } from "react";
import type { Chart, Chat, FeedEvent, Source } from "./api";
import { ChartBody } from "./charts/ChartBody";
import { date, number } from "./charts/format";

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

const KINDS = { stat: "Number", table: "Table", bar: "Bar chart", line: "Line chart" } as const;

export function Workspace(props: {
  chart: Chart | undefined;
  source: Source | undefined;
  busy: boolean;
  onAsk: (text: string) => void;
}) {
  const { chart, source } = props;
  const [asTable, setAsTable] = useState(false);
  const [details, setDetails] = useState(false);
  useEffect(() => {
    setAsTable(false);
  }, [chart?.id]);

  if (!chart) {
    return (
      <section className="flex min-h-0 flex-col items-center justify-center gap-5 p-8 text-center">
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
  const run = chart.run;
  const canChart = chart.spec.kind !== "table";
  return (
    <section className="flex min-h-0 min-w-0 flex-col p-5 pb-3">
      <header className="mb-4 flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-lg font-semibold text-ink-strong">{chart.title}</h1>
          <p className="text-xs text-ink-faint">
            {KINDS[chart.spec.kind]}
            {run && ` · ${number(run.rows.length)} ${run.rows.length === 1 ? "row" : "rows"} · data as of ${run.data_version}`}
            {chart.revision > 1 && ` · revision ${chart.revision}`}
          </p>
        </div>
        {canChart && (
          <div className="flex rounded-md border border-line-strong p-0.5 text-xs" role="group" aria-label="View">
            {[false, true].map((table) => (
              <button
                key={String(table)}
                type="button"
                aria-pressed={asTable === table}
                onClick={() => setAsTable(table)}
                className={`cursor-pointer rounded px-2.5 py-1 ${asTable === table ? "bg-raised text-ink-strong" : "text-ink-muted hover:text-ink"}`}
              >
                {table ? "Table" : "Chart"}
              </button>
            ))}
          </div>
        )}
        <button
          type="button"
          aria-expanded={details}
          onClick={() => setDetails((open) => !open)}
          className={`cursor-pointer rounded-md border px-2.5 py-1.5 text-xs ${details ? "border-primary text-ink-strong" : "border-line-strong text-ink-muted hover:text-ink"}`}
        >
          How this was computed
        </button>
      </header>
      <div className={`min-h-0 flex-1 transition-opacity ${props.busy ? "opacity-50" : ""}`}>
        <ChartBody chart={chart} asTable={asTable} />
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

const OPS: Record<string, string> = { create_chart: "Created", update_chart: "Updated", show_chart: "Showing" };

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
            <Line key={event.id} who={event.kind} text={event.text ?? ""} flagged={event.kind === "assistant" && event.verified === false} />
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

function Line({ who, text, flagged = false }: { who: "user" | "assistant"; text: string; flagged?: boolean }) {
  return (
    <div className="flex gap-3">
      <span className={`w-5 shrink-0 pt-0.5 text-xs font-medium ${who === "user" ? "text-ink-faint" : "text-primary-bright"}`}>
        {who === "user" ? "You" : <Wave size={18} />}
      </span>
      <p className={`max-w-3xl leading-relaxed ${who === "user" ? "text-ink-muted" : "text-ink-strong"}`}>
        {text}
        {flagged && <span className="ml-2 text-xs text-warning">unverified</span>}
      </p>
    </div>
  );
}

export function ChartList(props: { charts: Chart[]; focus: string | null; onFocus: (id: string) => void }) {
  return (
    <aside className="flex min-h-0 flex-col border-l border-line bg-surface">
      <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-ink-faint uppercase">Charts</h2>
      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-2" aria-label="Charts in this chat">
        {props.charts.length === 0 && <p className="px-2.5 text-sm text-ink-faint">Charts you create appear here.</p>}
        {props.charts.map((chart, i) => (
          <button
            key={chart.id}
            type="button"
            onClick={() => props.onFocus(chart.id)}
            aria-current={chart.id === props.focus ? "true" : undefined}
            className={`flex w-full cursor-pointer gap-2.5 rounded-md px-2.5 py-2 text-left ${chart.id === props.focus ? "bg-raised text-ink-strong" : "text-ink-muted hover:bg-raised/50 hover:text-ink"}`}
          >
            <span className="w-4 shrink-0 text-xs leading-5 text-ink-faint tabular-nums">{i + 1}</span>
            <span className="min-w-0">
              <span className="block text-sm leading-5">{chart.title}</span>
              <span className="block text-xs text-ink-faint">{KINDS[chart.spec.kind]}</span>
            </span>
          </button>
        ))}
      </nav>
    </aside>
  );
}
