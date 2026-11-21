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
  /** Assistant only: the model behind each stage (route, plan, reply) that used one. */
  models?: Record<string, string> | null;
  /** Assistant only: seconds spent in each stage. */
  seconds?: Record<string, number> | null;
};
/** What a chat runs on. Null effort leaves reasoning to the model; a null reply model means the planner replies. */
export type Settings = {
  model: string;
  effort: string | null;
  reply_model: string | null;
  reply_effort: string | null;
  router: boolean;
  voice: VoiceSettings;
};
/** How a chat listens and speaks in voice mode. */
export type VoiceSettings = {
  listen_model: string;
  /** ISO 639 code of the language spoken; null detects it. */
  language: string | null;
  /** Read replies aloud. */
  speak: boolean;
  /** Null uses a built-in default voice. */
  voice_id: string | null;
  speak_model: string;
  /** 0.7 to 1.2. */
  speed: number;
};
export type Voice = { id: string; name: string; description: string; preview_url: string | null };
export type Voices = { voices: Voice[]; note: string | null };
export type SettingsInfo = { defaults: Settings; efforts: string[]; keys: Record<string, boolean> };
export type ModelInfo = { id: string; name: string };

export type Chat = { id: string; source: string; title: string; created_at: string };
export type ChatView = { chat: Chat; settings: Settings; charts: Chart[]; events: FeedEvent[]; shown: string[] };
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
    headers: init?.headers ?? (init?.body ? { "Content-Type": "application/json" } : undefined),
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

const put = (body: unknown): RequestInit => ({ method: "PUT", body: JSON.stringify(body) });
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
  /** The stage a turn in progress has reached: "route", "plan", "speak", or null. */
  progress: (id: string) => request<{ stage: string | null }>(`/api/chats/${id}/progress`),
  /** `shown` is the charts on screen, so "it" means what the user is looking at. */
  say: (id: string, text: string, shown: string[]) => request<TurnView>(`/api/chats/${id}/turns`, post({ text, shown })),
  setView: (id: string, chartId: string, kind: Kind) =>
    request<Chart>(`/api/chats/${id}/charts/${chartId}/view`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ kind }),
    }),
  resetLens: (id: string, chartId: string) =>
    request<void>(`/api/chats/${id}/charts/${chartId}/lens`, { method: "DELETE" }),
  voices: () => request<Voices>("/api/voices"),
  /** The words in a recording. The audio goes to the talkviz server, which holds the speech key. */
  transcribe: (audio: Blob, chatId: string | null) =>
    request<{ text: string }>(`/api/transcribe${chatId ? `?chat=${chatId}` : ""}`, { method: "POST", body: audio, headers: { "Content-Type": audio.type || "audio/webm" } }),
  /** Where a reply can be heard, read aloud in the chat's voice. */
  replyAudio: (id: string, eventId: number) => `/api/chats/${id}/events/${eventId}/audio`,
  voiceSample: (voiceId: string, speed: number) => `/api/voices/${encodeURIComponent(voiceId)}/sample?speed=${speed}`,
  settings: () => request<SettingsInfo>("/api/settings"),
  models: () => request<ModelInfo[]>("/api/models"),
  saveDefaults: (settings: Settings) => request<Settings>("/api/settings", put(settings)),
  saveSettings: (id: string, settings: Settings) => request<Settings>(`/api/chats/${id}/settings`, put(settings)),
  rerun: (id: string, chartId: string) =>
    request<Chart>(`/api/chats/${id}/charts/${chartId}/run`, post()),
};
