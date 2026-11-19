import { useEffect, useId, useState } from "react";
import type { ModelInfo, Settings, SettingsInfo } from "./api";

/** "anthropic/claude-sonnet-5.5" as "claude-sonnet-5.5". */
export const short = (model: string) => model.slice(model.indexOf("/") + 1);

/** One line saying what a chat runs on, for the button that opens its settings. */
export function summary(settings: Settings): string {
  const effort = (value: string | null) => (value ? ` (${value})` : "");
  const parts = [`${short(settings.model)}${effort(settings.effort)}`];
  if (settings.reply_model) parts.push(`replies: ${short(settings.reply_model)}${effort(settings.reply_effort)}`);
  if (!settings.router) parts.push("fast path off");
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
  /** The open chat's title; null when editing the defaults. */
  chat: string | null;
  onSave: (settings: Settings, asDefault: boolean) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(props.settings);
  const [asDefault, setAsDefault] = useState(false);
  const set = (changes: Partial<Settings>) => setDraft((d) => ({ ...d, ...changes }));
  const changed = JSON.stringify(draft) !== JSON.stringify(props.settings);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && props.onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props.onClose]);

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/60 p-4" onClick={props.onClose}>
      <form
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        className="flex max-h-full w-full max-w-lg flex-col rounded-lg border border-line-strong bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          if (draft.model.trim()) props.onSave(draft, asDefault || props.chat === null);
        }}
      >
        <header className="flex items-baseline justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-ink-strong">Settings</h2>
            <p className="mt-0.5 text-xs text-ink-faint">
              {props.chat === null ? "Defaults for new chats" : `For this chat: ${props.chat}`}
            </p>
          </div>
          <button type="button" onClick={props.onClose} aria-label="Close settings" className="cursor-pointer px-1 text-lg text-ink-faint hover:text-ink-strong">
            ×
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-4">
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

          <Section title="API keys" hint="Read from the environment where talkviz runs; they are never shown or set here.">
            <ul className="space-y-1 text-sm">
              <Key name="OpenRouter" variable="OPENROUTER_API_KEY" set={props.info.keys.openrouter ?? false} />
              <Key name="ElevenLabs" variable="ELEVENLABS_API_KEY" set={props.info.keys.elevenlabs ?? false} />
            </ul>
          </Section>
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
              disabled={!draft.model.trim() || (!changed && !asDefault)}
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

function Section(props: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <section>
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
