import { useEffect, useId, useRef, useState } from "react";
import { api, type ModelInfo, type Settings, type SettingsInfo, type Voices, type VoiceSettings } from "./api";

/** "anthropic/claude-sonnet-5.5" as "claude-sonnet-5.5". */
export const short = (model: string) => model.slice(model.indexOf("/") + 1);

/** One line saying what a chat runs on, for the button that opens its settings. */
export function summary(settings: Settings, mode: "chat" | "voice" = "chat"): string {
  const effort = (value: string | null) => (value ? ` (${value})` : "");
  const parts = [`${short(settings.model)}${effort(settings.effort)}`];
  if (settings.reply_model) parts.push(`replies: ${short(settings.reply_model)}${effort(settings.reply_effort)}`);
  if (!settings.router) parts.push("fast path off");
  if (mode === "voice") parts.push(settings.voice.speak ? `spoken at ${settings.voice.speed.toFixed(2)}×` : "replies not spoken");
  return parts.join(" · ");
}

/**
 * The settings of the open chat, or with no chat open, the defaults a new
 * chat starts with. Changes are held here until saved.
 */
export function SettingsPanel(props: {
  settings: Settings;
  info: SettingsInfo;
  models: ModelInfo[];
  /** The voices to choose from; null until they have loaded. */
  voices: Voices | null;
  /** The chat these settings are for, by title; null when editing the defaults. */
  chat: string | null;
  /** Where the panel sits: mid-screen, or just above the question box it was opened from. */
  anchor?: "center" | "bar";
  /** Put the voice settings first, as when opened in voice mode. */
  voiceFirst?: boolean;
  onSave: (settings: Settings, asDefault: boolean) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(props.settings);
  const [asDefault, setAsDefault] = useState(false);
  const set = (changes: Partial<Settings>) => setDraft((d) => ({ ...d, ...changes }));
  const setVoice = (changes: Partial<VoiceSettings>) => setDraft((d) => ({ ...d, voice: { ...d.voice, ...changes } }));
  const hasSpeech = props.info.keys.elevenlabs ?? false;
  const changed = JSON.stringify(draft) !== JSON.stringify(props.settings);
  const language = draft.voice.language;
  const valid = Boolean(draft.model.trim() && draft.voice.listen_model && draft.voice.speak_model && (language === null || language.length >= 2));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && props.onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props.onClose]);

  return (
    <div
      className={`fixed inset-0 z-20 flex p-4 ${props.anchor === "bar" ? "items-end justify-start pb-24 pl-[256px]" : "items-center justify-center bg-black/60"}`}
      onClick={props.onClose}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        className={`flex max-h-full w-full flex-col rounded-lg border border-line-strong bg-surface shadow-xl ${props.anchor === "bar" ? "max-w-md shadow-black/60" : "max-w-lg"}`}
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) props.onSave(draft, asDefault || props.chat === null);
        }}
      >
        <header className="flex items-baseline justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-ink-strong">{props.chat === null ? "Settings" : "This chat"}</h2>
            <p className="mt-0.5 text-xs text-ink-faint">
              {props.chat === null ? "What new chats start with. Chats you already have keep their own." : props.chat}
            </p>
          </div>
          <button type="button" onClick={props.onClose} aria-label="Close settings" className="cursor-pointer px-1 text-lg text-ink-faint hover:text-ink-strong">
            ×
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-5 py-4">
          <Section title="Planning" hint="Turns what you ask into queries and charts.">
            <ModelField label="Model" value={draft.model} models={props.models} onChange={(model) => set({ model })} />
            <EffortField value={draft.effort} efforts={props.info.efforts} onChange={(effort) => set({ effort })} />
          </Section>

          <Section title="Replies" hint="Writes what is said back about each result.">
            <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={draft.reply_model === null}
                onChange={(e) => set(e.target.checked ? { reply_model: null, reply_effort: null } : { reply_model: draft.model })}
              />
              Use the planning model
            </label>
            {draft.reply_model !== null && (
              <>
                <ModelField label="Model" value={draft.reply_model} models={props.models} onChange={(reply_model) => set({ reply_model })} />
                <EffortField value={draft.reply_effort} efforts={props.info.efforts} onChange={(reply_effort) => set({ reply_effort })} />
              </>
            )}
          </Section>

          <Section title="Fast path" hint="A small decision model handles redraws, zooming, showing a chart again and questions about a chart without planning.">
            <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
              <input type="checkbox" checked={draft.router} onChange={(e) => set({ router: e.target.checked })} />
              Use the fast path
            </label>
          </Section>

          <Section
            first={props.voiceFirst}
            title="Voice"
            hint={hasSpeech ? "Used in voice mode: hold the space bar to talk, and replies are read aloud." : "Voice mode needs ELEVENLABS_API_KEY set where talkviz runs."}
          >
            <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
              <input type="checkbox" checked={draft.voice.speak} onChange={(e) => setVoice({ speak: e.target.checked })} />
              Read replies aloud
            </label>
            <VoiceField value={draft.voice} voices={props.voices} canPlay={hasSpeech} onChange={setVoice} />
            <label className="block text-xs text-ink-muted">
              Speaking speed: {draft.voice.speed.toFixed(2)}×
              <input
                type="range"
                min={0.7}
                max={1.2}
                step={0.05}
                value={draft.voice.speed}
                onChange={(e) => setVoice({ speed: Number(e.target.value) })}
                className="mt-1 block w-full accent-[var(--color-primary)]"
              />
            </label>
            <label className="block text-xs text-ink-muted">
              Language you speak
              <input
                value={draft.voice.language ?? ""}
                onChange={(e) => setVoice({ language: e.target.value.trim().toLowerCase() || null })}
                placeholder="Detected automatically (or a code such as en)"
                maxLength={3}
                className={`mt-1 ${FIELD}`}
              />
            </label>
            <details className="text-xs text-ink-muted">
              <summary className="cursor-pointer">Speech models</summary>
              <div className="mt-2 space-y-3">
                <label className="block">
                  Listening
                  <input value={draft.voice.listen_model} onChange={(e) => setVoice({ listen_model: e.target.value.trim() })} spellCheck={false} className={`mt-1 font-mono ${FIELD}`} />
                </label>
                <label className="block">
                  Speaking
                  <input value={draft.voice.speak_model} onChange={(e) => setVoice({ speak_model: e.target.value.trim() })} spellCheck={false} className={`mt-1 font-mono ${FIELD}`} />
                </label>
              </div>
            </details>
          </Section>

          {props.chat === null && (
            <Section title="API keys" hint="Read from the environment where talkviz runs; they are never shown or set here.">
              <ul className="space-y-1 text-sm">
                <Key name="OpenRouter" variable="OPENROUTER_API_KEY" set={props.info.keys.openrouter ?? false} />
                <Key name="ElevenLabs" variable="ELEVENLABS_API_KEY" set={props.info.keys.elevenlabs ?? false} />
              </ul>
            </Section>
          )}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-line px-5 py-3">
          {props.chat !== null ? (
            <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-muted">
              <input type="checkbox" checked={asDefault} onChange={(e) => setAsDefault(e.target.checked)} />
              Also use for new chats
            </label>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <button type="button" onClick={props.onClose} className="cursor-pointer rounded-md border border-line-strong px-3 py-1.5 text-sm text-ink hover:bg-raised">
              Cancel
            </button>
            <button
              type="submit"
              disabled={!valid || (!changed && !asDefault)}
              className="cursor-pointer rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-bright disabled:cursor-default disabled:opacity-40"
            >
              Save
            </button>
          </div>
        </footer>
      </form>
    </div>
  );
}

function Section(props: { title: string; hint: string; first?: boolean; children: React.ReactNode }) {
  return (
    <section className={props.first ? "order-first" : undefined}>
      <h3 className="text-sm font-medium text-ink-strong">{props.title}</h3>
      <p className="mt-0.5 mb-3 text-xs text-ink-faint">{props.hint}</p>
      <div className="space-y-3">{props.children}</div>
    </section>
  );
}

const FIELD = "w-full rounded-md border border-line-strong bg-bg px-2.5 py-1.5 text-sm text-ink-strong focus:border-primary focus:outline-none";

/** Any OpenRouter model id, with the listed models offered as you type. */
function ModelField(props: { label: string; value: string; models: ModelInfo[]; onChange: (value: string) => void }) {
  const id = useId();
  const known = props.models.length === 0 || props.models.some((m) => m.id === props.value);
  return (
    <label className="block text-xs text-ink-muted">
      {props.label}
      <input
        value={props.value}
        onChange={(e) => props.onChange(e.target.value.trim())}
        list={id}
        spellCheck={false}
        placeholder="provider/model"
        className={`mt-1 font-mono ${FIELD}`}
      />
      <datalist id={id}>
        {props.models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </datalist>
      {!known && props.value && <span className="mt-1 block text-ink-faint">Not in OpenRouter's list; it will be tried as typed.</span>}
    </label>
  );
}

/** The voice replies are read in, with a button to hear it at the chosen speed. */
function VoiceField(props: { value: VoiceSettings; voices: Voices | null; canPlay: boolean; onChange: (changes: Partial<VoiceSettings>) => void }) {
  const playing = useRef<HTMLAudioElement | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => () => playing.current?.pause(), []);
  const listed = props.voices?.voices ?? [];
  const chosen = props.value.voice_id ?? listed[0]?.id ?? "";
  const hear = () => {
    playing.current?.pause();
    setFailed(false);
    const audio = new Audio(api.voiceSample(chosen, props.value.speed));
    playing.current = audio;
    audio.play().catch(() => setFailed(true));
  };
  return (
    <div className="text-xs text-ink-muted">
      <label htmlFor="voice">Voice</label>
      <div className="mt-1 flex gap-2">
        <select id="voice" value={chosen} onChange={(e) => props.onChange({ voice_id: e.target.value })} className={FIELD}>
          {/* A saved voice the list does not have is still shown, by its id. */}
          {chosen && !listed.some((v) => v.id === chosen) && <option value={chosen}>{chosen}</option>}
          {listed.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
              {v.description && ` (${v.description})`}
            </option>
          ))}
        </select>
        <button type="button" onClick={hear} disabled={!props.canPlay || !chosen} className="shrink-0 cursor-pointer rounded-md border border-line-strong px-3 text-sm text-ink hover:bg-raised disabled:cursor-default disabled:opacity-40">
          Hear it
        </button>
      </div>
      {failed && <span className="mt-1 block text-warning">That voice could not be played.</span>}
      {props.voices?.note && <span className="mt-1 block text-ink-faint">Showing built-in voices; your account's could not be listed. {props.voices.note}</span>}
    </div>
  );
}

function EffortField(props: { value: string | null; efforts: string[]; onChange: (value: string | null) => void }) {
  return (
    <label className="block text-xs text-ink-muted">
      Reasoning effort
      <select value={props.value ?? ""} onChange={(e) => props.onChange(e.target.value || null)} className={`mt-1 ${FIELD}`}>
        <option value="">Model's own default</option>
        {props.efforts.map((effort) => (
          <option key={effort} value={effort}>
            {effort}
          </option>
        ))}
      </select>
    </label>
  );
}

function Key(props: { name: string; variable: string; set: boolean }) {
  return (
    <li className="flex items-baseline justify-between gap-3">
      <span className="text-ink">
        {props.name} <span className="font-mono text-xs text-ink-faint">{props.variable}</span>
      </span>
      <span className={props.set ? "text-xs text-ink-muted" : "text-xs text-warning"}>{props.set ? "✓ set" : "not set"}</span>
    </li>
  );
}
