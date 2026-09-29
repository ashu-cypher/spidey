import { useCallback, useEffect, useRef, useState } from 'react';

// ---------------------------------------------------------------------------
// Phase 6 — Voice interface, built entirely on the free browser Web Speech API.
// No paid APIs, no API keys, no backend changes: the backend already speaks
// plain text in/out, so voice is a pure frontend layer.
// ---------------------------------------------------------------------------

// Minimal typings for the Web Speech API (not in TS's DOM lib).
interface SpeechRecognitionAlternative {
  transcript: string;
  confidence: number;
}
interface SpeechRecognitionResult {
  isFinal: boolean;
  length: number;
  [index: number]: SpeechRecognitionAlternative;
}
interface SpeechRecognitionResultList {
  length: number;
  [index: number]: SpeechRecognitionResult;
}
interface SpeechRecognitionEvent extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}
interface SpeechRecognitionErrorEvent extends Event {
  error: string;
  message?: string;
}
interface SpeechRecognitionInstance extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
}

declare global {
  interface Window {
    SpeechRecognition?: new () => SpeechRecognitionInstance;
    webkitSpeechRecognition?: new () => SpeechRecognitionInstance;
  }
}

export interface SpeechRecognitionState {
  /** API available in this browser (false on non-HTTPS / headless). */
  supported: boolean;
  listening: boolean;
  /** Live interim transcript while the user is talking. */
  transcript: string;
  /** Completed final transcript (accumulates across results). */
  finalTranscript: string;
  error: string | null;
  start(): void;
  stop(): void;
  clear(): void;
}

export function useSpeechRecognition(): SpeechRecognitionState {
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [finalTranscript, setFinalTranscript] = useState('');
  const [error, setError] = useState<string | null>(null);
  const recRef = useRef<SpeechRecognitionInstance | null>(null);

  const supported =
    typeof window !== 'undefined' &&
    Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);

  const stop = useCallback(() => {
    recRef.current?.stop();
  }, []);

  const clear = useCallback(() => {
    setTranscript('');
    setFinalTranscript('');
    setError(null);
  }, []);

  const start = useCallback(() => {
    if (!supported || recRef.current) return;
    const Ctor = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Ctor) return;
    const rec = new Ctor();
    rec.continuous = false;
    rec.interimResults = true;
    rec.lang = 'en-US';
    clear();
    rec.onresult = (event: SpeechRecognitionEvent) => {
      let interim = '';
      let final = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const text = result[0]?.transcript ?? '';
        if (result.isFinal) final += text;
        else interim += text;
      }
      if (final) setFinalTranscript((prev) => prev + final);
      setTranscript(interim);
    };
    rec.onerror = (event: SpeechRecognitionErrorEvent) => {
      setError(
        event.error === 'not-allowed'
          ? 'Microphone permission denied.'
          : `Speech recognition failed (${event.error || 'unknown'}).`,
      );
    };
    rec.onend = () => {
      recRef.current = null;
      setListening(false);
      setTranscript('');
    };
    recRef.current = rec;
    try {
      rec.start();
      setListening(true);
    } catch {
      recRef.current = null;
      setListening(false);
    }
  }, [supported, clear]);

  // Abort any in-flight recognition if the component unmounts.
  useEffect(
    () => () => {
      recRef.current?.abort();
      recRef.current = null;
    },
    [],
  );

  return { supported, listening, transcript, finalTranscript, error, start, stop, clear };
}

/**
 * Lightly clean assistant text before reading it aloud: drop code blocks,
 * markdown formatting, links, citations like [1], and bare URLs.
 */
export function stripForSpeech(raw: string): string {
  return raw
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/(^|\n)#{1,6}\s+/g, '$1')
    .replace(/\[\d+\]/g, ' ')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export interface TextToSpeechState {
  /** API available in this browser (false on non-HTTPS / headless). */
  supported: boolean;
  speaking: boolean;
  speak(text: string): void;
  stop(): void;
  replay(): void;
}

export function useTextToSpeech(): TextToSpeechState {
  const [speaking, setSpeaking] = useState(false);
  const lastText = useRef('');

  const supported = typeof window !== 'undefined' && 'speechSynthesis' in window;

  const stop = useCallback(() => {
    if (supported) window.speechSynthesis.cancel();
    setSpeaking(false);
  }, [supported]);

  const speak = useCallback(
    (raw: string) => {
      if (!supported) return;
      const text = stripForSpeech(raw);
      if (!text) return;
      lastText.current = text;
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.onend = () => setSpeaking(false);
      utter.onerror = () => setSpeaking(false);
      window.speechSynthesis.speak(utter);
      setSpeaking(true);
    },
    [supported],
  );

  const replay = useCallback(() => {
    if (lastText.current) speak(lastText.current);
  }, [speak]);

  // Cancel speech if the component unmounts.
  useEffect(() => () => stop(), [stop]);

  return { supported, speaking, speak, stop, replay };
}
