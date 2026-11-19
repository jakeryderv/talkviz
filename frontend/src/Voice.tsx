import { useEffect, useRef, useState } from "react";
import { api } from "./api";

type Phase = "idle" | "listening" | "transcribing";

const BARS = 96; // levels kept, one drawn bar each
const SHORTEST = 300; // ms; a shorter hold is a slip, not speech

/** A text field or control that the space bar belongs to. */
const typing = (target: EventTarget | null) =>
  target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

/**
 * The voice-mode input: hold the space bar (or press and hold the bar) to
 * talk, and watch your voice as you do. Letting go sends what was said.
 */
export function VoiceBar(props: {
  /** The open chat, whose voice settings apply; null before one exists. */
  chatId: string | null;
  /** A turn is in progress; talking waits for it. */
  busy: boolean;
  /** Stop any reply being read aloud: the user is talking over it. */
  onInterrupt: () => void;
  onSay: (text: string) => void;
  onError: (message: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [blocked, setBlocked] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const mic = useRef<{ stream: MediaStream; analyser: AnalyserNode; context: AudioContext } | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const began = useRef(0);
  const levels = useRef<number[]>([]);
  const live = useRef({ phase, busy: props.busy, props });
  live.current = { phase, busy: props.busy, props };

  // The microphone is held for as long as voice mode is on, so talking
  // starts the moment the key goes down.
  useEffect(() => {
    let gone = false;
    if (!navigator.mediaDevices?.getUserMedia) {
      setBlocked("This browser cannot use the microphone here.");
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      .then((stream) => {
        if (gone) return stream.getTracks().forEach((track) => track.stop());
        const context = new AudioContext();
        const analyser = context.createAnalyser();
        analyser.fftSize = 1024;
        context.createMediaStreamSource(stream).connect(analyser);
        mic.current = { stream, analyser, context };
      })
      .catch(() => setBlocked("Microphone access was not allowed. Allow it for this page to use voice mode."));
    return () => {
      gone = true;
      recorder.current?.state === "recording" && recorder.current.stop();
      mic.current?.stream.getTracks().forEach((track) => track.stop());
      mic.current?.context.close();
      mic.current = null;
    };
  }, []);

  const start = () => {
    const { phase, busy, props } = live.current;
    if (!mic.current || phase !== "idle" || busy) return;
    props.onInterrupt();
    setHint(null);
    mic.current.context.resume();
    const chunks: Blob[] = [];
    const rec = new MediaRecorder(mic.current.stream);
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = () => {
      const held = performance.now() - began.current;
      if (held < SHORTEST || chunks.length === 0) {
        setPhase("idle");
        setHint("Hold the space bar while you talk.");
        return;
      }
      setPhase("transcribing");
      api
        .transcribe(new Blob(chunks, { type: rec.mimeType }), live.current.props.chatId)
        .then(({ text }) => (text ? live.current.props.onSay(text) : setHint("I didn't catch that.")))
        .catch((e) => live.current.props.onError(e instanceof Error ? e.message : String(e)))
        .finally(() => setPhase("idle"));
    };
    levels.current = [];
    began.current = performance.now();
    recorder.current = rec;
    rec.start();
    setPhase("listening");
  };
  const stop = () => recorder.current?.state === "recording" && recorder.current.stop();

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code !== "Space" || typing(e.target)) return;
      e.preventDefault(); // also keeps a focused button from being pressed
      if (!e.repeat) start();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space" || typing(e.target)) return;
      e.preventDefault();
      stop();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", stop);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", stop);
    };
  }, []);

  // The waveform: while listening, the loudness of each moment as a bar,
  // newest on the right. Otherwise a quiet line.
  useEffect(() => {
    let frame = 0;
    const samples = new Uint8Array(1024);
    const draw = () => {
      frame = requestAnimationFrame(draw);
      const el = canvas.current;
      const g = el?.getContext("2d");
      if (!el || !g) return;
      const scale = window.devicePixelRatio || 1;
      const [w, h] = [el.clientWidth, el.clientHeight];
      if (el.width !== w * scale || el.height !== h * scale) [el.width, el.height] = [w * scale, h * scale];
      g.setTransform(scale, 0, 0, scale, 0, 0);
      g.clearRect(0, 0, w, h);
      const style = getComputedStyle(el);
      const listening = live.current.phase === "listening";
      if (listening && mic.current) {
        mic.current.analyser.getByteTimeDomainData(samples);
        let sum = 0;
        for (const s of samples) sum += ((s - 128) / 128) ** 2;
        levels.current.push(Math.min(1, Math.sqrt(sum / samples.length) * 4));
        if (levels.current.length > BARS) levels.current.shift();
      }
      const step = w / BARS;
      g.fillStyle = style.getPropertyValue(listening ? "--color-primary-bright" : "--color-line-strong");
      for (let i = 0; i < BARS; i++) {
        const level = listening ? (levels.current[i - (BARS - levels.current.length)] ?? 0) : 0;
        const bar = Math.max(2, level * (h - 8));
        g.beginPath();
        g.roundRect(i * step + step * 0.2, (h - bar) / 2, step * 0.6, bar, step * 0.3);
        g.fill();
      }
    };
    draw();
    return () => cancelAnimationFrame(frame);
  }, []);

  const label =
    blocked ??
    (phase === "listening"
      ? "Listening… let go to send"
      : phase === "transcribing"
        ? "Transcribing…"
        : props.busy
          ? "Working on it…"
          : (hint ?? "Hold the space bar to talk"));

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label="Hold to talk"
      aria-pressed={phase === "listening"}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        start();
      }}
      onPointerUp={stop}
      onPointerCancel={stop}
      className="relative h-11 cursor-pointer touch-none rounded-t-xl select-none"
    >
      <canvas ref={canvas} className="absolute inset-0 h-full w-full" />
      <span className={`pointer-events-none absolute inset-0 flex items-center justify-center text-sm ${blocked ? "text-warning" : phase === "listening" ? "sr-only" : "text-ink-muted"}`} aria-live="polite">
        {label}
      </span>
    </div>
  );
}
