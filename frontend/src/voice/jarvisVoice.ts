import { useEffect, useRef } from 'react';
import { stripForSpeech } from '../hooks/useVoice';

// ---------------------------------------------------------------------------
// J.A.R.V.I.S. — voice engine built on the free browser Web Speech API.
// - Continuous recognition with interim results.
// - Wake-word gating: 'sleeping' mode only reacts to "jarvis"/"hey jarvis",
//   then stays 'awake' for 60 s of activity.
// - Barge-in: user speech while J.A.R.V.I.S. is talking cancels speech and
//   keeps the mic open.
// - speak(): British voice preference (Google UK English Male > Daniel >
//   en-GB > default), pitch/rate from localStorage.
// Correct-by-construction: cannot be mic-tested in headless environments.
// ---------------------------------------------------------------------------

// Minimal structural typings for the Web Speech API (not in TS's DOM lib).
interface JarvisAlternative {
  transcript: string;
  confidence: number;
}
interface JarvisResult {
  isFinal: boolean;
  length: number;
  [index: number]: JarvisAlternative;
}
interface JarvisResultList {
  length: number;
  [index: number]: JarvisResult;
}
interface JarvisRecognitionEvent {
  resultIndex: number;
  results: JarvisResultList;
}
interface JarvisRecognitionError {
  error: string;
  message?: string;
}
interface JarvisRecognition {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: JarvisRecognitionEvent) => void) | null;
  onerror: ((event: JarvisRecognitionError) => void) | null;
  onend: (() => void) | null;
}
type RecognitionCtor = new () => JarvisRecognition;

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export type WakeMode = 'awake' | 'sleeping';
export type WakeSensitivity = 'lenient' | 'strict';

/**
 * Explicit voice-engine state:
 * - idle: recognition off.
 * - listening: mic open, waiting for speech.
 * - recognizing: speech detected (interim or final), being transcribed.
 * - thinking: a voice-originated message is in flight (set by the UI layer).
 * - speaking: TTS is actively talking.
 * - error: a fatal recognition failure (see getVoiceError()).
 */
export type VoiceState =
  | 'idle'
  | 'listening'
  | 'recognizing'
  | 'thinking'
  | 'speaking'
  | 'error';

export interface JarvisVoiceCallbacks {
  /** Final + interim transcripts. In 'sleeping' mode, only post-wake-word commands. */
  onTranscript?: (text: string, isFinal: boolean) => void;
  onListeningChange?: (listening: boolean) => void;
  onModeChange?: (mode: WakeMode) => void;
  onSpeakingChange?: (speaking: boolean) => void;
  /** Emitted on every VoiceState transition. */
  onVoiceStateChange?: (state: VoiceState) => void;
  onError?: (message: string) => void;
  onUnsupported?: () => void;
  /** User speech detected while J.A.R.V.I.S. was speaking (barge-in). */
  onBargeIn?: () => void;
  /** The TTS phrase queue drained completely (natural end, not cancel). */
  onTtsQueueDrained?: () => void;
}

const AWAKE_WINDOW_MS = 60_000;
const WAKE_KEY = 'jarvis.wakeSensitivity';
const PITCH_KEY = 'jarvis.pitch';
const RATE_KEY = 'jarvis.rate';
const VOICE_URI_KEY = 'jarvis.voiceURI';
export const VOICE_LANG_KEY = 'jarvis.voiceLang';
const VOLUME_KEY = 'jarvis.volume';
export const VOLUME_STORAGE_KEY = VOLUME_KEY;

/** Voice language setting: recognition locale + TTS voice preference. */
export type VoiceLangSetting = 'auto' | 'en' | 'hi' | 'hinglish';

/**
 * Browsers cannot do true dual-language recognition, so Hinglish and Auto
 * both use en-IN; Auto additionally follows the last detected language for
 * the TTS voice choice (see the Voice tab note).
 */
function recognitionLocale(lang: VoiceLangSetting): string {
  switch (lang) {
    case 'en':
      return 'en-IN';
    case 'hi':
      return 'hi-IN';
    case 'hinglish':
    case 'auto':
    default:
      return 'en-IN';
  }
}

function readVoiceLangSetting(): VoiceLangSetting {
  try {
    const raw = localStorage.getItem(VOICE_LANG_KEY);
    if (raw === 'en' || raw === 'hi' || raw === 'hinglish' || raw === 'auto') {
      return raw;
    }
  } catch {
    /* ignore */
  }
  return 'auto';
}

function clamp(n: number, lo: number, hi: number): number {
  if (Number.isNaN(n)) return 1;
  return Math.min(hi, Math.max(lo, n));
}

function readNumber(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    return clamp(parseFloat(raw), 0.5, 2);
  } catch {
    return fallback;
  }
}

function readVolume(): number {
  try {
    const raw = localStorage.getItem(VOLUME_KEY);
    if (raw === null) return 1;
    const n = parseFloat(raw);
    if (Number.isNaN(n)) return 1;
    return Math.min(1, Math.max(0, n));
  } catch {
    return 1;
  }
}

/** Prefer a British voice: stored override > Google UK English Male > Daniel > en-GB > none. */
export function pickBritishVoice(
  voices: SpeechSynthesisVoice[],
): SpeechSynthesisVoice | null {
  if (voices.length === 0) return null;
  try {
    const stored = localStorage.getItem(VOICE_URI_KEY);
    if (stored) {
      const match = voices.find((v) => v.voiceURI === stored);
      if (match) return match;
    }
  } catch {
    /* ignore */
  }
  return (
    voices.find((v) => v.name.includes('Google UK English Male')) ??
    voices.find((v) => v.name.includes('Daniel')) ??
    voices.find((v) => v.lang.toLowerCase().startsWith('en-gb')) ??
    null
  );
}

export function listVoices(): SpeechSynthesisVoice[] {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return [];
  return window.speechSynthesis.getVoices();
}

/**
 * Pick a TTS voice for a response language. Hindi/Hinglish responses prefer
 * a voice whose lang starts with 'hi'; everything else keeps the British
 * preference. Never throws; returns null when no voice matches, in which
 * case the utterance falls back to the browser default.
 */
export function pickVoiceForLang(
  lang: string | null,
  voices: SpeechSynthesisVoice[],
): SpeechSynthesisVoice | null {
  if (lang === 'hi' || lang === 'hinglish') {
    const hindi = voices.find((v) => v.lang.toLowerCase().startsWith('hi'));
    if (hindi) return hindi;
  }
  return pickBritishVoice(voices);
}

interface QueuedSpeech {
  text: string;
  /** Response language hint ('hi'/'hinglish' prefer a Hindi voice). */
  lang: string | null;
  onDone?: () => void;
}

export class JarvisVoice {
  readonly supported: boolean;
  readonly ttsSupported: boolean;

  private cb: JarvisVoiceCallbacks = {};
  private rec: JarvisRecognition | null = null;
  private wantListening = false;
  private listening = false;
  private mode: WakeMode = 'sleeping';
  private awakeTimer: number | null = null;
  private restartTimer: number | null = null;
  private restartStamps: number[] = [];
  private sensitivity: WakeSensitivity;
  private recognitionLang: VoiceLangSetting;
  /** Conversation mode: the awake window never expires on its own. */
  private stayAwake = false;
  private speaking = false;
  private lastSpoken = '';
  private voiceState: VoiceState = 'idle';
  private voiceError: string | null = null;
  private recognizeTimer: number | null = null;
  private ttsQueue: QueuedSpeech[] = [];
  private ttsActive = false;

  constructor() {
    this.supported = recognitionCtor() !== null;
    this.ttsSupported =
      typeof window !== 'undefined' && 'speechSynthesis' in window;
    let s: WakeSensitivity = 'lenient';
    try {
      const raw = localStorage.getItem(WAKE_KEY);
      if (raw === 'strict' || raw === 'lenient') s = raw;
    } catch {
      /* ignore */
    }
    this.sensitivity = s;
    this.recognitionLang = readVoiceLangSetting();
  }

  // -- configuration -------------------------------------------------------

  setCallbacks(cb: JarvisVoiceCallbacks): void {
    this.cb = { ...this.cb, ...cb };
  }

  getMode(): WakeMode {
    return this.mode;
  }

  getSensitivity(): WakeSensitivity {
    return this.sensitivity;
  }

  setSensitivity(s: WakeSensitivity): void {
    this.sensitivity = s;
    try {
      localStorage.setItem(WAKE_KEY, s);
    } catch {
      /* ignore */
    }
  }

  getRecognitionLang(): VoiceLangSetting {
    return this.recognitionLang;
  }

  /**
   * Set the speech-recognition language (en→en-IN, hi→hi-IN,
   * hinglish/auto→en-IN). Applies to the live recognizer when possible and
   * to every recognizer spawned afterwards.
   */
  setRecognitionLang(lang: VoiceLangSetting): void {
    this.recognitionLang = lang;
    try {
      localStorage.setItem(VOICE_LANG_KEY, lang);
    } catch {
      /* ignore */
    }
    if (this.rec) {
      try {
        this.rec.lang = recognitionLocale(lang);
      } catch {
        /* changing lang mid-recognition may throw; next spawn applies it */
      }
    }
  }

  /** Conversation mode keeps the awake window from expiring on silence. */
  setStayAwake(v: boolean): void {
    this.stayAwake = v;
  }

  isSpeaking(): boolean {
    return this.speaking;
  }

  isListening(): boolean {
    return this.listening;
  }

  /** Current voice-engine state (see VoiceState). */
  getVoiceState(): VoiceState {
    return this.voiceState;
  }

  /** Human-readable description of the last fatal recognition error. */
  getVoiceError(): string | null {
    return this.voiceError;
  }

  private setVoiceState(next: VoiceState): void {
    if (this.voiceState === next) return;
    this.voiceState = next;
    if (next !== 'error') this.voiceError = null;
    this.cb.onVoiceStateChange?.(next);
  }

  /** Leave 'recognizing' once speech has paused for a beat. */
  private scheduleRecognizeCooldown(): void {
    if (this.recognizeTimer !== null) {
      window.clearTimeout(this.recognizeTimer);
      this.recognizeTimer = null;
    }
    this.recognizeTimer = window.setTimeout(() => {
      this.recognizeTimer = null;
      if (this.voiceState === 'recognizing') {
        this.setVoiceState(this.listening ? 'listening' : 'idle');
      }
    }, 2500);
  }

  private clearRecognizeCooldown(): void {
    if (this.recognizeTimer !== null) {
      window.clearTimeout(this.recognizeTimer);
      this.recognizeTimer = null;
    }
  }

  // -- recognition ---------------------------------------------------------

  /** Begin continuous listening. First user gesture should call this. */
  start(): void {
    if (!this.supported) {
      this.cb.onUnsupported?.();
      return;
    }
    this.wantListening = true;
    if (this.rec) return; // already running
    this.spawnRecognition();
  }

  stop(): void {
    this.wantListening = false;
    this.clearRecognizeCooldown();
    if (this.restartTimer !== null) {
      window.clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    try {
      this.rec?.stop();
    } catch {
      /* ignore */
    }
  }

  /** Clear an error state and attempt to listen again. */
  retry(): void {
    this.voiceError = null;
    if (this.voiceState === 'error') this.setVoiceState('idle');
    this.stop();
    this.start();
  }

  destroy(): void {
    this.stop();
    this.clearRecognizeCooldown();
    if (this.awakeTimer !== null) {
      window.clearTimeout(this.awakeTimer);
      this.awakeTimer = null;
    }
    this.stopSpeaking();
    this.rec = null;
  }

  /** Fully release the mic: no restart, drop the recognizer instance. */
  releaseMicrophone(): void {
    this.wantListening = false;
    this.clearRecognizeCooldown();
    if (this.restartTimer !== null) {
      window.clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    const rec = this.rec;
    this.rec = null;
    try {
      rec?.abort();
    } catch {
      /* ignore */
    }
    this.setListening(false);
  }

  /** Restart recognition if it silently died while the mic is still wanted. */
  ensureListening(): void {
    if (!this.supported || !this.wantListening || this.rec) return;
    this.spawnRecognition();
  }

  private spawnRecognition(): void {
    const Ctor = recognitionCtor();
    if (!Ctor) {
      this.cb.onUnsupported?.();
      return;
    }
    const rec: JarvisRecognition = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = recognitionLocale(this.recognitionLang);

    rec.onresult = (event) => this.handleResult(event);
    rec.onerror = (event) => this.handleError(event);
    rec.onend = () => this.handleEnd();

    this.rec = rec;
    try {
      rec.start();
      this.setListening(true);
    } catch {
      this.rec = null;
      this.setListening(false);
    }
  }

  private setListening(listening: boolean): void {
    if (this.listening === listening) return;
    this.listening = listening;
    this.cb.onListeningChange?.(listening);
    if (listening) {
      if (this.voiceState !== 'error') this.setVoiceState('listening');
    } else if (this.voiceState === 'listening' || this.voiceState === 'recognizing') {
      this.clearRecognizeCooldown();
      this.setVoiceState('idle');
    }
  }

  private setMode(mode: WakeMode): void {
    if (this.mode === mode) return;
    this.mode = mode;
    this.cb.onModeChange?.(mode);
  }

  private handleResult(event: JarvisRecognitionEvent): void {
    let interim = '';
    let final = '';
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      const text = result[0]?.transcript ?? '';
      if (result.isFinal) final += text;
      else interim += text;
    }
    const heard = (final || interim).trim();
    if (!heard) return;

    // Barge-in: user talking while J.A.R.V.I.S. speaks cancels speech,
    // recognition keeps running. The UI layer also aborts the in-flight
    // chat stream via onBargeIn so the new utterance is processed fresh.
    if (this.speaking) {
      this.stopSpeaking();
      this.cb.onBargeIn?.();
    }

    // Speech detected — mark 'recognizing'; a cooldown drops back to
    // 'listening' once the user pauses.
    if (this.voiceState !== 'error') {
      this.setVoiceState('recognizing');
      this.scheduleRecognizeCooldown();
    }

    if (this.mode === 'sleeping') {
      const hay = (final || interim).toLowerCase();
      if (this.detectWakeWord(hay)) {
        this.wakeUp();
        // Emit whatever came after the wake word as the command.
        const command = this.stripWakeWord(final || interim);
        if (command.trim()) this.cb.onTranscript?.(command.trim(), Boolean(final));
      }
      return;
    }

    // Awake: forward everything; refresh the 60 s window on final results.
    if (final) this.refreshAwakeWindow();
    this.cb.onTranscript?.(heard, Boolean(final));
  }

  private handleError(event: JarvisRecognitionError): void {
    const code = event.error ?? '';
    if (code === 'not-allowed' || code === 'service-not-allowed') {
      this.wantListening = false;
      this.failRecognition(
        'Microphone access denied — J.A.R.V.I.S. cannot listen.',
      );
    } else if (code === 'audio-capture') {
      this.wantListening = false;
      this.failRecognition('No microphone was found on this device.');
    } else if (code === 'network') {
      this.wantListening = false;
      this.failRecognition(
        'The speech service is unreachable — check your connection.',
      );
    } else if (code === 'aborted' || code === 'no-speech') {
      // Benign: onend will restart when wantListening is set.
    } else if (code) {
      this.failRecognition(`Speech recognition error: ${code}`);
    }
  }

  /** Enter the 'error' voice state with a human-readable message. */
  private failRecognition(message: string): void {
    this.clearRecognizeCooldown();
    this.voiceError = message;
    this.setVoiceState('error');
    this.cb.onError?.(message);
  }

  private handleEnd(): void {
    this.rec = null;
    this.setListening(false);
    if (!this.wantListening) return;
    // Guard against hot restart loops (e.g. repeated immediate errors).
    const now = Date.now();
    this.restartStamps = this.restartStamps.filter((t) => now - t < 2000);
    this.restartStamps.push(now);
    const delay = this.restartStamps.length > 5 ? 2000 : 300;
    if (this.restartTimer !== null) window.clearTimeout(this.restartTimer);
    this.restartTimer = window.setTimeout(() => {
      this.restartTimer = null;
      if (this.wantListening && !this.rec) this.spawnRecognition();
    }, delay);
  }

  // -- wake word -----------------------------------------------------------

  private detectWakeWord(lowerText: string): boolean {
    if (this.sensitivity === 'strict') {
      return /^\s*(hey[,\s]+)?jarvis\b/.test(lowerText);
    }
    return lowerText.includes('jarvis');
  }

  private stripWakeWord(text: string): string {
    return text.replace(/^\s*(hey[,\s]+)?jarvis[,\s]*/i, '');
  }

  /** Switch to awake mode for 60 s (called on wake word or manual activation). */
  wakeUp(): void {
    this.setMode('awake');
    this.refreshAwakeWindow();
  }

  /** Drop back to sleeping (wake-word only) immediately. */
  sleep(): void {
    if (this.awakeTimer !== null) {
      window.clearTimeout(this.awakeTimer);
      this.awakeTimer = null;
    }
    this.setMode('sleeping');
  }

  private refreshAwakeWindow(): void {
    if (this.awakeTimer !== null) window.clearTimeout(this.awakeTimer);
    this.awakeTimer = window.setTimeout(() => {
      this.awakeTimer = null;
      if (this.stayAwake) {
        // Conversation mode: re-arm instead of falling asleep on silence.
        this.refreshAwakeWindow();
        return;
      }
      this.setMode('sleeping');
    }, AWAKE_WINDOW_MS);
  }

  // -- speech --------------------------------------------------------------

  /**
   * Queue a phrase to be spoken after already-queued phrases (phrase-wise
   * TTS). `lang` picks the voice: 'hi'/'hinglish' prefer a Hindi voice.
   * Never throws; silently drops the phrase when TTS is unsupported.
   */
  enqueueSpeech(
    raw: string,
    opts?: { lang?: string | null; onDone?: () => void },
  ): void {
    if (!this.ttsSupported) {
      try {
        opts?.onDone?.();
      } catch {
        /* ignore */
      }
      return;
    }
    const text = stripForSpeech(raw);
    if (!text) {
      try {
        opts?.onDone?.();
      } catch {
        /* ignore */
      }
      return;
    }
    this.ttsQueue.push({ text, lang: opts?.lang ?? null, onDone: opts?.onDone });
    this.pumpTtsQueue();
  }

  private pumpTtsQueue(): void {
    if (this.ttsActive || !this.ttsSupported) return;
    const item = this.ttsQueue.shift();
    if (!item) {
      this.cb.onTtsQueueDrained?.();
      return;
    }
    this.ttsActive = true;
    const synth = window.speechSynthesis;
    try {
      synth.cancel();
    } catch {
      /* ignore */
    }
    const utter = new SpeechSynthesisUtterance(item.text);
    const voice = pickVoiceForLang(item.lang, synth.getVoices());
    if (voice) utter.voice = voice;
    utter.lang =
      voice?.lang ?? (item.lang === 'hi' || item.lang === 'hinglish' ? 'hi-IN' : 'en-GB');
    utter.pitch = readNumber(PITCH_KEY, 1);
    utter.rate = readNumber(RATE_KEY, 1);
    utter.volume = readVolume();
    this.lastSpoken = item.text;
    utter.onstart = () => this.setSpeaking(true);
    const finish = () => {
      this.ttsActive = false;
      try {
        item.onDone?.();
      } catch {
        /* a callback must never break the queue */
      }
      // Keep the 'speaking' state across phrases; drop it only when the
      // queue is truly empty so the reactor doesn't flicker.
      if (this.ttsQueue.length === 0) this.setSpeaking(false);
      this.pumpTtsQueue();
    };
    utter.onend = finish;
    utter.onerror = finish;
    try {
      synth.speak(utter);
    } catch {
      finish();
    }
  }

  /** Speak text aloud, cancelling anything already queued or speaking. */
  speak(raw: string): void {
    this.stopSpeaking();
    this.enqueueSpeech(raw);
  }

  /** Cancel queued and in-flight speech immediately. */
  stopSpeaking(): void {
    this.ttsQueue = [];
    this.ttsActive = false;
    if (!this.ttsSupported) return;
    try {
      window.speechSynthesis.cancel();
    } catch {
      /* ignore */
    }
    this.setSpeaking(false);
  }

  replayLast(): void {
    if (this.lastSpoken) this.speak(this.lastSpoken);
  }

  private setSpeaking(speaking: boolean): void {
    if (this.speaking === speaking) return;
    this.speaking = speaking;
    this.cb.onSpeakingChange?.(speaking);
    if (speaking) {
      this.clearRecognizeCooldown();
      this.setVoiceState('speaking');
    } else if (this.voiceState === 'speaking') {
      this.setVoiceState(this.listening ? 'listening' : 'idle');
    }
  }
}

/** React hook owning a single JarvisVoice for the app lifetime. */
export function useJarvisVoice(
  callbacks?: JarvisVoiceCallbacks,
): { voice: JarvisVoice; supported: boolean; ttsSupported: boolean } {
  const voiceRef = useRef<JarvisVoice | null>(null);
  if (!voiceRef.current) voiceRef.current = new JarvisVoice();
  const cbRef = useRef<JarvisVoiceCallbacks | undefined>(callbacks);
  cbRef.current = callbacks;

  useEffect(() => {
    const voice = voiceRef.current as JarvisVoice;
    voice.setCallbacks({
      onTranscript: (t, f) => cbRef.current?.onTranscript?.(t, f),
      onListeningChange: (l) => cbRef.current?.onListeningChange?.(l),
      onModeChange: (m) => cbRef.current?.onModeChange?.(m),
      onSpeakingChange: (s) => cbRef.current?.onSpeakingChange?.(s),
      onVoiceStateChange: (s) => cbRef.current?.onVoiceStateChange?.(s),
      onError: (e) => cbRef.current?.onError?.(e),
      onUnsupported: () => cbRef.current?.onUnsupported?.(),
      onBargeIn: () => cbRef.current?.onBargeIn?.(),
      onTtsQueueDrained: () => cbRef.current?.onTtsQueueDrained?.(),
    });
    return () => {
      voice.destroy();
    };
  }, []);

  const v = voiceRef.current as JarvisVoice;
  return { voice: v, supported: v.supported, ttsSupported: v.ttsSupported };
}
