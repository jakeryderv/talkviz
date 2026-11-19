import { type ReactNode, useEffect, useRef, useState } from "react";

/** State that is remembered in this browser. Works, unremembered, where storage is unavailable. */
export function useStored<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = localStorage.getItem(key);
      return saved === null ? initial : (JSON.parse(saved) as T);
    } catch {
      return initial;
    }
  });
  const set = (next: T) => {
    setValue(next);
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch {
      // Not remembered; nothing else depends on it.
    }
  };
  return [value, set];
}

const QUIET = "cursor-pointer rounded-md text-ink-muted hover:bg-raised hover:text-ink-strong disabled:cursor-default disabled:opacity-40";

/** A button that is only an icon; `label` names it for tooltips and screen readers. */
export function IconButton(props: { label: string; onClick: () => void; children: ReactNode; pressed?: boolean; className?: string }) {
  return (
    <button
      type="button"
      aria-label={props.label}
      title={props.label}
      aria-pressed={props.pressed}
      onClick={props.onClick}
      className={`flex size-7 shrink-0 items-center justify-center ${QUIET} ${props.pressed ? "bg-raised text-ink-strong" : ""} ${props.className ?? ""}`}
    >
      {props.children}
    </button>
  );
}

export type MenuItem = { key: string; label: string; checked?: boolean; onSelect: () => void };

/** A button that opens a short list of choices. Closes on a choice, a click elsewhere, or Escape. */
export function Menu(props: { label: ReactNode; name: string; items: MenuItem[]; align?: "left" | "right"; up?: boolean; className?: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("pointerdown", outside);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("pointerdown", outside);
      window.removeEventListener("keydown", escape);
    };
  }, [open]);
  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-label={props.name}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
        className={`flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs ${open ? "border-primary text-ink-strong" : "border-line-strong text-ink hover:border-ink-faint hover:text-ink-strong"} cursor-pointer ${props.className ?? ""}`}
      >
        {props.label}
      </button>
      {open && (
        <div
          role="menu"
          className={`absolute z-30 min-w-40 rounded-lg border border-line-strong bg-raised p-1 text-sm shadow-xl shadow-black/50 ${props.align === "left" ? "left-0" : "right-0"} ${props.up ? "bottom-full mb-1" : "mt-1"}`}
        >
          {props.items.map((item) => (
            <button
              key={item.key}
              type="button"
              role={item.checked === undefined ? "menuitem" : "menuitemradio"}
              aria-checked={item.checked}
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
              className={`flex w-full cursor-pointer items-center justify-between gap-4 rounded-md px-2.5 py-1.5 text-left whitespace-nowrap hover:bg-line-strong hover:text-ink-strong ${item.checked ? "text-ink-strong" : "text-ink"}`}
            >
              {item.label}
              {item.checked && <span className="size-1.5 rounded-full bg-primary-bright" aria-hidden />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
