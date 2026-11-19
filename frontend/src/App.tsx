import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Chart, type Chat, type ChatView, type Kind, type ModelInfo, type Settings, type SettingsInfo, type Source, type Voices } from "./api";
import { ChartList, Feed, Sidebar, Workspace } from "./Panels";
import { SettingsPanel, summary } from "./Settings";

const MAX_SHOWN = 4;

function recalled(): "chat" | "voice" {
  try {
    return localStorage.getItem("talkviz.mode") === "voice" ? "voice" : "chat";
  } catch {
    return "chat";
  }
}

export function App() {
  const [sources, setSources] = useState<Source[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [view, setView] = useState<ChatView | null>(null);
  // The charts in the workspace; more than one is drawn side by side.
  const [shown, setShown] = useState<string[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<SettingsInfo | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [voices, setVoices] = useState<Voices | null>(null);
  const [mode, setMode] = useState<"chat" | "voice">(recalled);
  // The reply being read aloud, if one is.
  const speaking = useRef<HTMLAudioElement | null>(null);
  const hush = () => {
    speaking.current?.pause();
    speaking.current = null;
  };
  const choose = (next: "chat" | "voice") => {
    setMode(next);
    if (next === "chat") hush();
    try {
      localStorage.setItem("talkviz.mode", next);
    } catch {
      // Not remembered; nothing else depends on it.
    }
  };

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  useEffect(() => {
    api.sources().then(setSources).catch(fail);
    api.chats().then(setChats).catch(fail);
    api.settings().then(setInfo).catch(fail);
  }, []);
  // The model list is long and only the settings panel needs it.
  useEffect(() => {
    if (settingsOpen && models.length === 0) api.models().then(setModels).catch(() => {});
    if (settingsOpen && !voices) api.voices().then(setVoices).catch(() => {});
  }, [settingsOpen, models.length, voices]);

  const open = useCallback((id: string, chartIds?: string[]) => {
    setError(null);
    api
      .chat(id)
      .then((loaded) => {
        setView(loaded);
        setShown(chartIds?.length ? chartIds : loaded.shown);
      })
      .catch(fail);
  }, []);

  // The address holds the open chat and its charts (#chat/c1+c2), so a reload
  // or a bookmark comes back to the same place.
  useEffect(() => {
    const [chatId, charts] = location.hash.slice(1).split("/");
    if (chatId) open(chatId, charts?.split("+").filter(Boolean));
  }, [open]);
  useEffect(() => {
    const hash = view ? `#${view.chat.id}${shown.length ? `/${shown.join("+")}` : ""}` : "";
    if (location.hash !== hash) history.replaceState(null, "", hash || location.pathname);
  }, [view?.chat.id, shown.join()]);

  const startNew = () => {
    setView(null);
    setShown([]);
    setError(null);
  };

  const say = async (text: string) => {
    setError(null);
    setPending(text);
    try {
      const chat = view?.chat ?? (await api.newChat());
      const current = view ?? { chat, settings: info?.defaults ?? (await api.chat(chat.id)).settings, charts: [], events: [], shown: [] };
      if (!view) setView(current);
      const turn = await api.say(chat.id, text);
      const changed = new Map(turn.charts.map((c) => [c.id, c]));
      setView({
        chat: turn.chat,
        settings: current.settings,
        charts: [
          ...current.charts.map((c) => changed.get(c.id) ?? c),
          ...turn.charts.filter((c) => !current.charts.some((old) => old.id === c.id)),
        ],
        events: [...current.events, ...turn.events],
        shown: turn.shown.length ? turn.shown : current.shown,
      });
      if (turn.shown.length) setShown(turn.shown);
      // In voice mode the reply is read aloud, in the chat's voice.
      const reply = turn.events.findLast((e) => e.kind === "assistant" && e.text);
      if (mode === "voice" && reply && current.settings.voice.speak) {
        hush();
        const audio = new Audio(api.replyAudio(chat.id, reply.id));
        speaking.current = audio;
        audio.play().catch(() => speaking.current === audio && fail("The reply could not be read aloud. Check the voice in this chat's settings."));
      }
      setChats((all) => [turn.chat, ...all.filter((c) => c.id !== turn.chat.id)]);
    } catch (e) {
      fail(e);
    } finally {
      setPending(null);
    }
  };

  // Clicking a chart shows it and quietly re-runs it on the current data.
  const show = (chartId: string) => {
    setShown([chartId]);
    refresh(chartId);
  };
  // Adds a chart beside those showing, or takes it away.
  const toggle = (chartId: string) => {
    if (shown.includes(chartId)) return setShown(shown.filter((id) => id !== chartId));
    if (shown.length >= MAX_SHOWN) return;
    setShown([...shown, chartId]);
    refresh(chartId);
  };
  const refresh = (chartId: string) => {
    if (!view) return;
    const replace = (fresh: Chart) =>
      setView((v) => v && { ...v, charts: v.charts.map((c) => (c.id === fresh.id ? fresh : c)) });
    api.rerun(view.chat.id, chartId).then(replace).catch(fail);
  };

  // Drawing a chart another way is saved with the chart, but changes nothing
  // else: no query, no revision, nothing in the feed.
  const draw = (chartId: string, kind: Kind) => {
    if (!view) return;
    setView((v) => v && {
      ...v,
      charts: v.charts.map((c) => (c.id === chartId ? { ...c, view: kind === c.spec.kind ? null : kind } : c)),
    });
    api.setView(view.chat.id, chartId, kind).catch(fail);
  };

  // Puts the whole chart back in view.
  const reset = (chartId: string) => {
    if (!view) return;
    setView((v) => v && { ...v, charts: v.charts.map((c) => (c.id === chartId ? { ...c, lens: null } : c)) });
    api.resetLens(view.chat.id, chartId).catch(fail);
  };

  // Saves the open chat's settings, or with no chat open, the defaults.
  const saveSettings = (settings: Settings, asDefault: boolean) => {
    setSettingsOpen(false);
    if (view) {
      setView((v) => v && { ...v, settings });
      api.saveSettings(view.chat.id, settings).catch(fail);
    }
    if (asDefault) {
      setInfo((i) => i && { ...i, defaults: settings });
      api.saveDefaults(settings).catch(fail);
    }
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
  const charts = shown.flatMap((id) => view?.charts.find((c) => c.id === id) ?? []);
  const settings = view?.settings ?? info?.defaults;

  return (
    <div className="grid h-full grid-cols-[240px_minmax(0,1fr)_260px]">
      <Sidebar chats={chats} active={view?.chat.id ?? null} source={source} onOpen={(id) => open(id)} onNew={startNew} onDelete={remove} onSettings={() => setSettingsOpen(true)} />
      <main className="grid min-h-0 min-w-0 grid-cols-[minmax(0,1fr)] grid-rows-[minmax(0,1fr)_minmax(180px,32%)]">
        <Workspace charts={charts} all={view?.charts ?? []} source={source} busy={pending !== null} onAsk={say} onDraw={draw} onReset={reset} onClose={toggle} />
        <Feed events={view?.events ?? []} pending={pending} error={error} running={settings ? summary(settings) : null} mode={mode} chatId={view?.chat.id ?? null} onMode={choose} onInterrupt={hush} onError={setError} onSay={say} onFocus={show} onSettings={() => setSettingsOpen(true)} />
      </main>
      <ChartList charts={view?.charts ?? []} shown={shown} full={shown.length >= MAX_SHOWN} onFocus={show} onToggle={toggle} />
      {settingsOpen && settings && info && (
        <SettingsPanel
          settings={settings}
          info={info}
          models={models}
          voices={voices}
          chat={view ? view.chat.title : null}
          onSave={saveSettings}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  );
}
