import { useCallback, useEffect, useState } from "react";
import { api, type Chart, type Chat, type ChatView, type Source } from "./api";
import { ChartList, Feed, Sidebar, Workspace } from "./Panels";

export function App() {
  const [sources, setSources] = useState<Source[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [view, setView] = useState<ChatView | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  useEffect(() => {
    api.sources().then(setSources).catch(fail);
    api.chats().then(setChats).catch(fail);
  }, []);

  const open = useCallback((id: string, chartId?: string) => {
    setError(null);
    api
      .chat(id)
      .then((loaded) => {
        setView(loaded);
        setFocus(chartId ?? loaded.focus);
      })
      .catch(fail);
  }, []);

  // The address holds the open chat and chart (#chat/chart), so a reload or a
  // bookmark comes back to the same place.
  useEffect(() => {
    const [chatId, chartId] = location.hash.slice(1).split("/");
    if (chatId) open(chatId, chartId);
  }, [open]);
  useEffect(() => {
    const hash = view ? `#${view.chat.id}${focus ? `/${focus}` : ""}` : "";
    // Leave a chosen view (#chat/chart/view) alone while the chart is unchanged.
    if (!hash || !location.hash.startsWith(`${hash}/`)) {
      if (location.hash !== hash) history.replaceState(null, "", hash || location.pathname);
    }
  }, [view?.chat.id, focus]);

  const startNew = () => {
    setView(null);
    setFocus(null);
    setError(null);
  };

  const say = async (text: string) => {
    setError(null);
    setPending(text);
    try {
      const chat = view?.chat ?? (await api.newChat());
      const current = view ?? { chat, charts: [], events: [], focus: null };
      if (!view) setView(current);
      const turn = await api.say(chat.id, text);
      const changed = new Map(turn.charts.map((c) => [c.id, c]));
      setView({
        chat: turn.chat,
        charts: [
          ...current.charts.map((c) => changed.get(c.id) ?? c),
          ...turn.charts.filter((c) => !current.charts.some((old) => old.id === c.id)),
        ],
        events: [...current.events, ...turn.events],
        focus: turn.focus ?? current.focus,
      });
      if (turn.focus) setFocus(turn.focus);
      setChats((all) => [turn.chat, ...all.filter((c) => c.id !== turn.chat.id)]);
    } catch (e) {
      fail(e);
    } finally {
      setPending(null);
    }
  };

  // Clicking a chart shows it and quietly re-runs it on the current data.
  const show = (chartId: string) => {
    setFocus(chartId);
    if (!view) return;
    const replace = (fresh: Chart) =>
      setView((v) => v && { ...v, charts: v.charts.map((c) => (c.id === fresh.id ? fresh : c)) });
    api.rerun(view.chat.id, chartId).then(replace).catch(fail);
  };

  const remove = (id: string) => {
    api
      .deleteChat(id)
      .then(() => {
        setChats((all) => all.filter((c) => c.id !== id));
        if (view?.chat.id === id) startNew();
      })
      .catch(fail);
  };

  const source = sources.find((s) => s.name === view?.chat.source) ?? sources[0];
  const chart = view?.charts.find((c) => c.id === focus);

  return (
    <div className="grid h-full grid-cols-[240px_minmax(0,1fr)_260px]">
      <Sidebar chats={chats} active={view?.chat.id ?? null} source={source} onOpen={(id) => open(id)} onNew={startNew} onDelete={remove} />
      <main className="grid min-h-0 min-w-0 grid-cols-[minmax(0,1fr)] grid-rows-[minmax(0,1fr)_minmax(180px,32%)]">
        <Workspace chart={chart} source={source} busy={pending !== null} onAsk={say} />
        <Feed events={view?.events ?? []} pending={pending} error={error} onSay={say} onFocus={setFocus} />
      </main>
      <ChartList charts={view?.charts ?? []} focus={focus} onFocus={show} />
    </div>
  );
}
