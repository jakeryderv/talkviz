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
  | { kind: "bar" | "line"; x: string; y: string; series?: string | null };

export type Chart = {
  id: string;
  title: string;
  sql: string;
  spec: Spec;
  revision: number;
  run: Run | null;
};

export type FeedEvent = {
  id: number;
  at: string;
  kind: "user" | "assistant" | "chart";
  text?: string | null;
  verified?: boolean | null;
  op?: string | null;
  chart_id?: string | null;
  revision?: number | null;
  title?: string | null;
  run_id?: number | null;
};

export type Chat = { id: string; source: string; title: string; created_at: string };
export type ChatView = { chat: Chat; charts: Chart[]; events: FeedEvent[]; focus: string | null };
export type TurnView = { chat: Chat; events: FeedEvent[]; charts: Chart[]; focus: string | null };
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
  rerun: (id: string, chartId: string) =>
    request<Chart>(`/api/chats/${id}/charts/${chartId}/run`, post()),
};
