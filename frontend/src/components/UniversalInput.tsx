import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useMew } from '../mew/context';
import type { VoiceState } from '../voice/mewVoice';

interface Props {
  value: string;
  onChange: (v: string) => void;
  /** Receives the flushed draft text (debounce-safe). */
  onSend: (text: string) => void;
  pending: File[];
  onFiles: (files: File[]) => void;
  onRemovePending: (index: number) => void;
  busy: boolean;
  onStop: () => void;
  disabled?: boolean;
}

/** Debounce for the draft mirror — parent re-renders skip the chat list. */
const DRAFT_DEBOUNCE_MS = 140;

// ---------------------------------------------------------------------------
// Pixel-style micro-icons (crisp blocky glyphs, minecraft-subtle).
// ---------------------------------------------------------------------------

function PixelIcon({ children }: { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width="18"
      height="18"
      shapeRendering="crispEdges"
      aria-hidden="true"
    >
      <g fill="currentColor">{children}</g>
    </svg>
  );
}

function PixelMic() {
  return (
    <PixelIcon>
      <rect x="6" y="1" width="4" height="6" />
      <rect x="5" y="2" width="1" height="4" />
      <rect x="10" y="2" width="1" height="4" />
      <rect x="4" y="7" width="1" height="2" />
      <rect x="11" y="7" width="1" height="2" />
      <rect x="5" y="9" width="6" height="1" />
      <rect x="7" y="10" width="2" height="3" />
      <rect x="5" y="13" width="6" height="1" />
    </PixelIcon>
  );
}

function PixelAttach() {
  return (
    <PixelIcon>
      <rect x="5" y="1" width="6" height="1" />
      <rect x="10" y="1" width="1" height="9" />
      <rect x="5" y="9" width="6" height="1" />
      <rect x="5" y="1" width="1" height="4" />
      <rect x="7" y="4" width="4" height="1" />
      <rect x="7" y="4" width="1" height="4" />
      <rect x="7" y="8" width="4" height="1" />
      <rect x="10" y="5" width="1" height="3" />
    </PixelIcon>
  );
}

function PixelSend() {
  return (
    <PixelIcon>
      <rect x="2" y="7" width="6" height="2" />
      <rect x="6" y="5" width="2" height="6" />
      <rect x="8" y="3" width="2" height="10" />
      <rect x="10" y="1" width="2" height="14" />
      <rect x="12" y="3" width="1" height="10" />
    </PixelIcon>
  );
}

function PixelStop() {
  return (
    <PixelIcon>
      <rect x="4" y="4" width="8" height="8" />
    </PixelIcon>
  );
}

interface MicVisual {
  stop: boolean;
  label: string;
  title: string;
  pulse: boolean;
  danger: boolean;
}

function micVisual(state: VoiceState): MicVisual {
  switch (state) {
    case 'speaking':
      return { stop: true, label: 'Stop', title: 'Stop MEW speaking', pulse: false, danger: true };
    case 'listening':
      return { stop: false, label: 'Listening…', title: 'Listening — tap to stop', pulse: true, danger: false };
    case 'recognizing':
      return { stop: false, label: 'Heard…', title: 'Transcribing — tap to stop', pulse: true, danger: false };
    case 'thinking':
      return { stop: false, label: 'Thinking…', title: 'Working — tap to stop listening', pulse: true, danger: false };
    case 'error':
      return { stop: false, label: 'Retry', title: 'Voice error — tap to try again', pulse: false, danger: true };
    default:
      return { stop: false, label: 'Talk', title: 'Activate voice interface', pulse: false, danger: false };
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * The universal input bar: autosizing textarea + mic + attach + send/stop.
 * The textarea is a LOCAL debounced draft: keystrokes never re-render the
 * chat list, and send() always receives the flushed latest text.
 * Pending attachments render as removable chips above the textarea.
 */
export function UniversalInput({
  value,
  onChange,
  onSend,
  pending,
  onFiles,
  onRemovePending,
  busy,
  onStop,
  disabled,
}: Props) {
  const {
    voiceState,
    voiceSupported,
    toggleListening,
    retryVoice,
    stopSpeaking,
    registerChatInputApi,
    registerAttachApi,
  } = useMew();

  const areaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [draft, setDraft] = useState(value);
  const draftRef = useRef(draft);
  const lastExternalRef = useRef(value);
  const debounceRef = useRef<number | null>(null);

  // External value changes (prefill, clear-after-send) overwrite the draft;
  // our own debounced mirrors are ignored.
  useEffect(() => {
    if (value !== lastExternalRef.current) {
      lastExternalRef.current = value;
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      draftRef.current = value;
      setDraft(value);
    }
  }, [value]);

  useEffect(
    () => () => {
      if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    },
    [],
  );

  const commitDraft = (v: string) => {
    draftRef.current = v;
    setDraft(v);
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      debounceRef.current = null;
      lastExternalRef.current = v;
      onChange(v);
    }, DRAFT_DEBOUNCE_MS);
  };

  /** Flush any pending debounce and return the latest text. */
  const flushDraft = (): string => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    const text = draftRef.current;
    lastExternalRef.current = text;
    onChange(text);
    return text;
  };

  const handleSend = () => {
    if (disabled || busy) return;
    const text = flushDraft();
    if (!text.trim() && pending.length === 0) return;
    onSend(text);
  };

  // Autosize: grow with content up to ~6 lines, then scroll.
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 148)}px`;
  }, [draft]);

  // Expose prefill/focus for quick-action chips and voice.
  useEffect(() => {
    registerChatInputApi({
      prefill: (text: string) => {
        onChange(text);
        areaRef.current?.focus();
      },
      focus: () => areaRef.current?.focus(),
    });
    return () => registerChatInputApi(null);
  }, [registerChatInputApi, onChange]);

  // Expose the attach picker for quick-action chips.
  useEffect(() => {
    registerAttachApi({ open: () => fileRef.current?.click() });
    return () => registerAttachApi(null);
  }, [registerAttachApi]);

  const onMic = () => {
    if (voiceState === 'speaking') stopSpeaking();
    else if (voiceState === 'error') retryVoice();
    else toggleListening();
  };
  const v = micVisual(voiceState);

  const canSend = !disabled && (draft.trim().length > 0 || pending.length > 0);

  return (
    <div className="rounded-2xl border border-accent/25 bg-carbon/70 shadow-[0_0_32px_rgba(56,225,255,0.08)] backdrop-blur">
      {pending.length > 0 && (
        <div className="flex flex-wrap gap-2 border-b border-accent/10 px-4 pt-3">
          {pending.map((f, i) => (
            <span
              key={`${f.name}-${f.size}-${i}`}
              className="mb-2 inline-flex max-w-full items-center gap-2 rounded-full border border-gold/40 bg-gold/10 px-3 py-1 text-xs text-gold"
            >
              <span aria-hidden="true">📎</span>
              <span className="max-w-[180px] truncate font-medium">{f.name}</span>
              <span className="font-mono text-[10px] text-gold/60">{formatSize(f.size)}</span>
              <button
                type="button"
                onClick={() => onRemovePending(i)}
                title="Remove attachment"
                aria-label={`Remove ${f.name}`}
                className="text-gold/60 hover:text-red-300"
              >
                ✕
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2 p-3">
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          aria-label="Attach files"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = '';
            if (files.length > 0) onFiles(files);
          }}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          title="Attach files (or drag & drop onto the conversation)"
          aria-label="Attach files"
          disabled={disabled}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-accent/30 bg-accent/5 text-cyan-200/80 transition-all hover:border-accent/60 hover:bg-accent/15 hover:text-accent disabled:opacity-30"
        >
          <PixelAttach />
        </button>

        <button
          type="button"
          onClick={onMic}
          disabled={!voiceSupported || disabled}
          title={voiceSupported ? v.title : 'Voice not supported in this browser — type instead'}
          aria-label={voiceSupported ? `${v.label} — ${v.title}` : 'Voice unavailable'}
          className={`relative flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border transition-all disabled:opacity-30 ${
            v.danger
              ? 'border-crimson/70 bg-crimson/10 text-red-300'
              : voiceState === 'idle'
                ? 'border-accent/30 bg-accent/5 text-cyan-200/80 hover:border-accent/60 hover:bg-accent/15 hover:text-accent'
                : 'border-accent/70 bg-accent/15 text-accent'
          }`}
        >
          {v.pulse && (
            <span className="absolute inset-0 rounded-xl border border-accent/50 hud-blink" aria-hidden="true" />
          )}
          {v.stop ? <PixelStop /> : <PixelMic />}
        </button>

        <textarea
          ref={areaRef}
          rows={1}
          value={draft}
          disabled={disabled}
          onChange={(e) => commitDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              if (canSend && !busy) handleSend();
            }
          }}
          placeholder="Message MEW… (Shift+Enter for a new line)"
          aria-label="Message MEW"
          className="max-h-[148px] min-h-[44px] flex-1 resize-none rounded-xl border border-transparent bg-transparent px-3 py-2.5 text-sm text-cyan-100 placeholder:text-cyan-200/30 focus:border-accent/40 focus:outline-none disabled:opacity-50"
        />

        {busy ? (
          <button
            type="button"
            onClick={onStop}
            title="Stop generating"
            aria-label="Stop generating"
            className="flex h-11 shrink-0 items-center gap-2 rounded-xl border border-crimson/70 bg-crimson/15 px-4 font-mono text-xs uppercase tracking-[0.2em] text-red-300 shadow-[0_0_24px_rgba(230,36,41,0.3)] hover:bg-crimson/25"
          >
            <PixelStop /> Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={handleSend}
            disabled={!canSend}
            title="Send"
            aria-label="Send message"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-accent/60 bg-accent/15 text-accent shadow-[0_0_24px_rgba(56,225,255,0.25)] transition-all hover:bg-accent/25 disabled:opacity-30 disabled:shadow-none"
          >
            <PixelSend />
          </button>
        )}
      </div>
    </div>
  );
}
