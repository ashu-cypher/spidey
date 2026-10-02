// ---------------------------------------------------------------------------
// Phrase-wise TTS for the streaming chat path.
//
// The backend contract guarantees `voice_summary` is ALWAYS a prefix of the
// full response. This module tracks that guarantee:
//  - summary(text): spoken immediately; remembered as the spoken prefix.
//  - delta(chunk): accumulate; once the accumulated text covers the summary,
//    split the remainder into complete sentences (. ! ? and Hindi ।) and
//    hand each newly completed sentence to TTS. An incomplete trailing
//    fragment waits for more deltas.
//  - flush(): at `done`, speak the trailing fragment so nothing is dropped.
//
// Dedup: every enqueued phrase is recorded (normalized) in `spokenSet`, so a
// sentence is never spoken twice even if the prefix alignment ever drifts.
// ---------------------------------------------------------------------------

export interface SplitResult {
  sentences: string[];
  /** Trailing fragment without a sentence terminator (may be ''). */
  rest: string;
}

/**
 * Split leading complete sentences off `text`. A sentence ends with
 * `.` `!` `?` or the Hindi danda `।`, optionally followed by a closing
 * quote/bracket. The trailing unterminated fragment is returned as `rest`.
 */
export function splitSentences(text: string): SplitResult {
  const sentences: string[] = [];
  const re = /([\s\S]*?[.!?।]["'”’)\]]?)(?=\s|$)/g;
  let lastEnd = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m[0].length === 0) break; // cannot happen; guards the loop
    const s = m[1].trim();
    if (s) sentences.push(s);
    lastEnd = m.index + m[0].length;
  }
  return { sentences, rest: text.slice(lastEnd) };
}

function norm(s: string): string {
  return s.replace(/\s+/g, ' ').trim().toLowerCase();
}

export type EnqueueSpeech = (text: string, lang: string | null) => void;

export class StreamSpeechTracker {
  /** Full accumulated display text (deltas concatenated). */
  fullText = '';
  /** Characters of fullText already handed to TTS. */
  spokenLen = 0;
  /** Summary received but not yet covered by deltas (null once aligned). */
  pendingSummary: string | null = null;
  /** Authoritative response language from the `done` event (may be null). */
  ttsLang: string | null = null;

  private spokenSet = new Set<string>();
  private enqueue: EnqueueSpeech;

  constructor(enqueue: EnqueueSpeech) {
    this.enqueue = enqueue;
  }

  /** A `voice_summary` event: speak immediately, remember as spoken prefix. */
  summary(text: string, hintLang: string | null): void {
    const t = text.trim();
    if (!t) return;
    this.pendingSummary = t;
    this.spokenSet.add(norm(t));
    this.enqueue(t, hintLang);
  }

  /** A `delta` event: accumulate and speak newly completed sentences. */
  delta(chunk: string): void {
    if (!chunk) return;
    this.fullText += chunk;
    if (this.pendingSummary !== null) {
      if (this.fullText.startsWith(this.pendingSummary)) {
        // Normal path: deltas caught up with the spoken summary.
        this.spokenLen = this.pendingSummary.length;
        this.pendingSummary = null;
      } else if (!this.pendingSummary.startsWith(this.fullText)) {
        // Contract violation (summary is not a prefix): fall back to
        // sentence splitting from the start; spokenSet dedups the summary.
        this.pendingSummary = null;
        this.spokenLen = 0;
      } else {
        // Deltas still catching up to the summary — wait for more.
        return;
      }
    }
    const remainder = this.fullText.slice(this.spokenLen);
    const { sentences, rest } = splitSentences(remainder);
    for (const s of sentences) {
      const key = norm(s);
      if (this.spokenSet.has(key)) continue;
      this.spokenSet.add(key);
      this.enqueue(s, this.ttsLang);
    }
    this.spokenLen = this.fullText.length - rest.length;
  }

  /** Stream finished: speak any trailing fragment so the tail is heard. */
  flush(): void {
    const rest = this.fullText.slice(this.spokenLen).trim();
    if (rest) {
      const key = norm(rest);
      if (!this.spokenSet.has(key)) {
        this.spokenSet.add(key);
        this.enqueue(rest, this.ttsLang);
      }
    }
    this.spokenLen = this.fullText.length;
  }
}
