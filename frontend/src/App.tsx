import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Chart, type Chat, type ChatView, type Kind, type ModelInfo, type Settings, type SettingsInfo, type Source, type Voices } from "./api";
import { ChartList, Feed, Sidebar, Workspace } from "./Panels";
import { SettingsPanel, summary } from "./Settings";
import { useStored } from "./ui";

const MAX_SHOWN = 4;
const RAIL = 44; // px, a collapsed side column
const FEED_SHARE = 0.34; // of the height, for the conversation

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
  // Which settings are open: the defaults for new chats, or this chat's own.
  const [settingsOpen, setSettingsOpen] = useState<"defaults" | "chat" | null>(null);
  // Settings chosen for a chat that has not been started yet.
  const [draft, setDraft] = useState<Settings | null>(null);
  // How far the turn in progress has got: "route", "plan" or "speak".
  const [stage, setStage] = useState<string | null>(null);
  const [voices, setVoices] = useState<Voices | null>(null);
  // How the screen is divided, remembered in this browser.
  const [chatsHidden, setChatsHidden] = useStored("talkviz.chatsHidden", false);
  const [listHidden, setListHidden] = useStored("talkviz.listHidden", false);
  const [feedShare, setFeedShare] = useStored("talkviz.feedShare", FEED_SHARE);
  const main = useRef<HTMLElement>(null);
  // Dragging the divider gives the conversation more or less of the height.
  const drag = (e: React.PointerEvent) => {
    const box = main.current?.getBoundingClientRect();
    if (!box || e.buttons !== 1) return;
    setFeedShare(Math.min(0.75, Math.max(0.18, (box.bottom - e.clientY) / box.height)));
  };
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
    const follow = () => {
      const [chatId, charts] = location.hash.slice(1).split("/");
      if (chatId) open(chatId, charts?.split("+").filter(Boolean));
    };
    follow();
    // Also when the address is edited or a link to another chat is followed.
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, [open]);
  useEffect(() => {
    const hash = view ? `#${view.chat.id}${shown.length ? `/${shown.join("+")}` : ""}` : "";
    if (location.hash !== hash) history.replaceState(null, "", hash || location.pathname);
  }, [view?.chat.id, shown.join()]);

  const startNew = () => {
    setView(null);
    setShown([]);
    setError(null);
    setDraft(null);
  };

  const say = async (text: string) => {
    setError(null);
    setPending(text);
    setStage(null);
    let watch: number | undefined;
    try {
      const chat = view?.chat ?? (await api.newChat());
      // A new chat takes the settings chosen for it before it existed, if any.
      const own = view ? null : draft && (await api.saveSettings(chat.id, draft));
      const current = view ?? { chat, settings: own ?? info?.defaults ?? (await api.chat(chat.id)).settings, charts: [], events: [], shown: [] };
      if (!view) setView(current);
      setDraft(null);
      // Ask how far the turn has got while it runs.
      watch = window.setInterval(() => api.progress(chat.id).then((p) => setStage(p.stage)).catch(() => {}), 400);
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
      window.clearInterval(watch);
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

  // Saves this chat's settings or the defaults, whichever panel was open. A
  // chat not yet started keeps its settings here until its first message.
  const saveSettings = (settings: Settings, asDefault: boolean) => {
    const scope = settingsOpen;
    setSettingsOpen(null);
    if (scope === "chat" && view) {
      setView((v) => v && { ...v, settings });
      api.saveSettings(view.chat.id, settings).catch(fail);
    } else if (scope === "chat") {
      setDraft(settings);
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
  const settings = view?.settings ?? draft ?? info?.defaults;

  return (
    <div className="grid h-full" style={{ gridTemplateColumns: `${chatsHidden ? RAIL : 240}px minmax(0,1fr) ${listHidden ? RAIL : 260}px` }}>
      <Sidebar collapsed={chatsHidden} onCollapse={setChatsHidden} chats={chats} active={view?.chat.id ?? null} source={source} onOpen={(id) => open(id)} onNew={startNew} onDelete={remove} onSettings={() => setSettingsOpen("defaults")} />
      <main ref={main} className="grid min-h-0 min-w-0 grid-cols-[minmax(0,1fr)]" style={{ gridTemplateRows: `minmax(0,1fr) 5px minmax(150px,${feedShare * 100}%)` }}>
        <Workspace charts={charts} all={view?.charts ?? []} source={source} busy={pending !== null} onAsk={say} onDraw={draw} onReset={reset} onClose={toggle} />
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize the conversation"
          title="Drag to resize; double-click to reset"
          onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)}
          onPointerMove={drag}
          onDoubleClick={() => setFeedShare(FEED_SHARE)}
          className="cursor-row-resize touch-none border-t border-line bg-surface hover:border-primary hover:bg-primary/30 active:border-primary active:bg-primary/40"
        />
        <Feed stage={stage} charts={view?.charts ?? []} events={view?.events ?? []} pending={pending} error={error} running={settings ? summary(settings, mode) : null} mode={mode} chatId={view?.chat.id ?? null} onMode={choose} onInterrupt={hush} onError={setError} onSay={say} onFocus={show} onSettings={() => setSettingsOpen("chat")} />
      </main>
      <ChartList collapsed={listHidden} onCollapse={setListHidden} charts={view?.charts ?? []} shown={shown} full={shown.length >= MAX_SHOWN} onFocus={show} onToggle={toggle} />
      {settingsOpen === "defaults" && info && (
        <SettingsPanel settings={info.defaults} info={info} models={models} voices={voices} chat={null} onSave={saveSettings} onClose={() => setSettingsOpen(null)} />
      )}
      {settingsOpen === "chat" && settings && info && (
        <SettingsPanel
          settings={settings}
          info={info}
          models={models}
          voices={voices}
          chat={view?.chat.title ?? "New chat"}
          anchor="bar"
          voiceFirst={mode === "voice"}
          onSave={saveSettings}
          onClose={() => setSettingsOpen(null)}
        />
      )}
    </div>
  );
}
