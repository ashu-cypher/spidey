import { useEffect, useRef, useState } from 'react';
import type { Attachment } from '../api';

// ---------------------------------------------------------------------------
// ContextIndicator — small 📎 count in the chat header area. Clicking opens a
// minimal popover listing the files in this conversation's context:
//  - pending files (not yet sent): removable — real, edits the pending list.
//  - already-sent attachments: shown for awareness; they are part of the
//    server-side context for this conversation, which resets when a new
//    conversation starts (no delete endpoint exists — never pretend).
// ---------------------------------------------------------------------------

interface Props {
  pending: File[];
  onRemovePending: (index: number) => void;
  sent: Attachment[];
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ContextIndicator({ pending, onRemovePending, sent }: Props) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const total = pending.length + sent.length;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('pointerdown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointerdown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open ]);

  if (total === 0) return null;

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        title="Conversation context — files attached in this conversation"
        className="inline-flex items-center gap-1.5 rounded-full border border-gold/40 bg-gold/10 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-gold transition-colors hover:bg-gold/20"
      >
        <span aria-hidden="true">📎</span>
        <span>
          {total} file{total === 1 ? '' : 's'}
        </span>
      </button>

      {open && (
        <div className="absolute right-0 z-40 mt-2 w-72 overflow-hidden rounded-xl border border-accent/25 bg-panel/95 shadow-[0_8px_32px_rgba(0,0,0,0.6)] backdrop-blur">
          <p className="border-b border-accent/10 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.22em] text-cyan-200/60">
            Context · this conversation
          </p>
          <div className="max-h-64 overflow-y-auto px-3 py-2">
            {pending.length > 0 && (
              <>
                <p className="py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-cyan-200/40">
                  Waiting to send
                </p>
                <ul className="space-y-1">
                  {pending.map((f, i) => (
                    <li
                      key={`${f.name}-${f.size}-${i}`}
                      className="flex items-center gap-2 text-xs text-cyan-100/85"
                    >
                      <span aria-hidden="true">📄</span>
                      <span className="flex-1 truncate">{f.name}</span>
                      <span className="font-mono text-[10px] text-cyan-200/40">
                        {formatSize(f.size)}
                      </span>
                      <button
                        type="button"
                        onClick={() => onRemovePending(i)}
                        title="Remove before sending"
                        aria-label={`Remove ${f.name}`}
                        className="px-1 text-cyan-200/40 hover:text-red-300"
                      >
                        ✕
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {sent.length > 0 && (
              <>
                <p className="py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-cyan-200/40">
                  Sent as context
                </p>
                <ul className="space-y-1">
                  {sent.map((a) => (
                    <li
                      key={a.id}
                      className="flex items-center gap-2 text-xs text-cyan-100/70"
                    >
                      <span aria-hidden="true">📎</span>
                      <span className="flex-1 truncate">{a.filename}</span>
                      <span className="font-mono text-[10px] text-cyan-200/40">
                        {a.kind}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
          <p className="border-t border-accent/10 px-3 py-2 text-[11px] text-cyan-200/40">
            Sent files are part of this conversation&apos;s context — start a
            new conversation to reset it.
          </p>
        </div>
      )}
    </div>
  );
}
