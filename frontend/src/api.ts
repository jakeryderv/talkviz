// Types mirror the Pydantic models in talkviz/server.py and talkviz/store.py.

export type ColumnKind = "number" | "time" | "text" | "boolean" | "other";
export type Column = { name: string; type: string; kind: ColumnKind };
export type Cell = string | number | boolean | null;

export type Run = {
  id: number;
  chart_id: string;
  revision: number;
  data_version: string;
  columns: Column[];
  rows: Cell[][];
  truncated: boolean;
  seconds: number;
  ran_at: string;
};

export type Spec =
  | { kind: "stat"; value: string; label: string }
  | { kind: "table" }
  | { kind: "bar" | "stacked_bar" | "line" | "area"; x: string; y: string; series?: string | null }
  | { kind: "donut" | "treemap"; label: string; value: string }
  | { kind: "heatmap"; x: string; y: string; value: string }
  | {
      kind: "scatter" | "scatter3d";
      x: string;
      y: string;
      z?: string | null;
      color?: string | null;
      label?: string | null;
    };
export type Kind = Spec["kind"];

/** A zoom along the x axis (both ends or neither) and the series not drawn. */
export type Lens = { start: string | null; end: string | null; hidden: string[] };

export type Condition = { column: string; values?: string[] | null; min?: string | null; max?: string | null };
export type Narrow = { where: Condition[]; top?: number | null; by?: string | null };

export type Chart = {
  id: string;
  title: string;
  sql: string;
  spec: Spec;
  revision: number;
  /** The chart this sub-chart was made from. Its id is the parent's plus a letter. */
  parent: string | null;
  /** Set when a sub-chart keeps rows of its parent's result instead of querying. */
  narrow: Narrow | null;
  /** The parent's revision a sub-chart with its own query was made against. */
  based_on: number | null;
  /** What part is in view, when not all of it. Saved with the chart. */
  lens: Lens | null;
  /** The kind it is drawn as, when not the spec's own. Saved with the chart. */
  view: Kind | null;
  run: Run | null;
  /** Every way the result can be drawn, the chart's own spec first. */
  views: Spec[];
};

export type FeedEvent = {
  id: number;
  at: string;
  kind: "user" | "assistant" | "chart";
  text?: string | null;
  verified?: boolean | null;
  /** Draft replies that failed the number check, with the figures at fault. */
  rejected?: { text: string; unsupported: string[] }[] | null;
  error?: string | null;
  op?: string | null;
  chart_id?: string | null;
  revision?: number | null;
  title?: string | null;
  run_id?: number | null;
};

export type Chat = { id: string; source: string; title: string; created_at: string };
export type ChatView = { chat: Chat; charts: Chart[]; events: FeedEvent[]; shown: string[] };
export type TurnView = { chat: Chat; events: FeedEvent[]; charts: Chart[]; shown: string[] };
export type Source = {
  name: string;
  title: string;
  description: string;
  data_version: string | null;
  examples: string[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // not JSON; keep the status text
    }
    throw new Error(detail);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  sources: () => request<Source[]>("/api/sources"),
  chats: () => request<Chat[]>("/api/chats"),
  chat: (id: string) => request<ChatView>(`/api/chats/${id}`),
  newChat: (source?: string) => request<Chat>("/api/chats", post({ source })),
  deleteChat: (id: string) => request<void>(`/api/chats/${id}`, { method: "DELETE" }),
  say: (id: string, text: string) => request<TurnView>(`/api/chats/${id}/turns`, post({ text })),
  setView: (id: string, chartId: string, kind: Kind) =>
    request<Chart>(`/api/chats/${id}/charts/${chartId}/view`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ kind }),
    }),
  resetLens: (id: string, chartId: string) =>
    request<void>(`/api/chats/${id}/charts/${chartId}/lens`, { method: "DELETE" }),
  rerun: (id: string, chartId: string) =>
    request<Chart>(`/api/chats/${id}/charts/${chartId}/run`, post()),
};
