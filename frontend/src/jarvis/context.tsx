import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import type { ReactorMode } from '../components/ArcReactor';
import { useJarvisVoice } from '../voice/jarvisVoice';
import type { JarvisVoice, WakeMode } from '../voice/jarvisVoice';
import { playSfx, setSfxEnabled, unlockAudio, useSpectrum } from '../audio/sfx';

export interface TranscriptLine {
  id: number;
  role: 'user' | 'jarvis' | 'system';
  text: string;
  at: string;
}

interface JarvisContextValue {
  voice: JarvisVoice;
  voiceSupported: boolean;
  ttsSupported: boolean;
  wakeMode: WakeMode;
  listening: boolean;
  /** True while J.A.R.V.I.S. is speaking (TTS). */
  speaking: boolean;
  /** Toggle continuous voice listening (handles wake-up + reactor mode). */
  toggleListening: () => void;
  reactorMode: ReactorMode;
  /** Temporarily override reactor mode; reverts after ms. */
  flashMode: (mode: ReactorMode, ms?: number) => void;
  setReactorMode: (mode: ReactorMode) => void;
  spectrumRef: { current: number[] };
  transcript: TranscriptLine[];
  logTranscript: (role: TranscriptLine['role'], text: string) => void;
  clearTranscript: () => void;
  sfxEnabled: boolean;
  setSfxEnabledState: (v: boolean) => void;
  /** Speak via the voice engine + reactor flare + transcript log. */
  speak: (text: string) => void;
  /** Registered by the chat UI; lets chips/voice send messages. */
  sendChat: (text: string) => void;
  registerChatSend: (fn: ((text: string) => void) | null) => void;
  chatBusy: boolean;
  setChatBusy: (b: boolean) => void;
  /** Bumped whenever a chat workflow completes (refreshes activity log). */
  activityTick: number;
  bumpActivity: () => void;
}

const JarvisContext = createContext<JarvisContextValue | null>(null);

export function useJarvis(): JarvisContextValue {
  const ctx = useContext(JarvisContext);
  if (!ctx) throw new Error('useJarvis must be used inside JarvisProvider');
  return ctx;
}

let lineId = 0;

export function JarvisProvider({ children }: { children: ReactNode }) {
  const [reactorMode, setReactorModeState] = useState<ReactorMode>('idle');
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [sfxEnabled, setSfxEnabledStateInner] = useState<boolean>(() => {
    try {
      return localStorage.getItem('jarvis.sfx') !== '0';
    } catch {
      return true;
    }
  });
  const [chatBusy, setChatBusy] = useState(false);
  const [activityTick, setActivityTick] = useState(0);
  const bumpActivity = useCallback(() => setActivityTick((t) => t + 1), []);
  const [wakeMode, setWakeMode] = useState<WakeMode>('sleeping');
  const [listening, setListening] = useState(false);
  const [speaking, setSpeakingState] = useState(false);
  const listeningRef = useRef(false);

  const modeRef = useRef<ReactorMode>('idle');
  const flashTimer = useRef<number | null>(null);
  const chatSendRef = useRef<((text: string) => void) | null>(null);

  const setReactorMode = useCallback((mode: ReactorMode) => {
    if (flashTimer.current !== null) {
      window.clearTimeout(flashTimer.current);
      flashTimer.current = null;
    }
    modeRef.current = mode;
    setReactorModeState(mode);
  }, []);

  const flashMode = useCallback((mode: ReactorMode, ms = 2000) => {
    if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    const prev = modeRef.current;
    modeRef.current = mode;
    setReactorModeState(mode);
    flashTimer.current = window.setTimeout(() => {
      flashTimer.current = null;
      modeRef.current = prev === 'alert' ? 'idle' : prev;
      setReactorModeState(modeRef.current);
    }, ms);
  }, []);

  const logTranscript = useCallback(
    (role: TranscriptLine['role'], text: string) => {
      const line: TranscriptLine = {
        id: lineId++,
        role,
        text,
        at: new Date().toLocaleTimeString(),
      };
      setTranscript((prev) => [...prev.slice(-99), line]);
    },
    [],
  );

  const clearTranscript = useCallback(() => setTranscript([]), []);

  const setSfxEnabledState = useCallback((v: boolean) => {
    setSfxEnabled(v);
    setSfxEnabledStateInner(v);
  }, []);

  const sendChat = useCallback((text: string) => {
    chatSendRef.current?.(text);
  }, []);

  const registerChatSend = useCallback(
    (fn: ((text: string) => void) | null) => {
      chatSendRef.current = fn;
    },
    [],
  );

  const { voice, supported: voiceSupported, ttsSupported } = useJarvisVoice({
    onTranscript: (text, isFinal) => {
      if (!isFinal) return;
      // The registered chat sender logs the transcript line itself.
      sendChat(text);
    },
    onListeningChange: (isListening) => {
      setListening(isListening);
      listeningRef.current = isListening;
      if (isListening) {
        modeRef.current = 'listening';
        setReactorModeState('listening');
      } else if (modeRef.current === 'listening') {
        modeRef.current = 'idle';
        setReactorModeState('idle');
      }
    },
    onModeChange: (mode) => {
      setWakeMode(mode);
      if (mode === 'awake') {
        playSfx('blip');
        logTranscript('system', 'Wake word detected — J.A.R.V.I.S. awake.');
      }
    },
    onSpeakingChange: (isSpeaking) => {
      // Reactor flares gold while J.A.R.V.I.S. talks; afterwards it returns
      // to listening/idle (never stuck on a transient flash mode).
      setSpeakingState(isSpeaking);
      if (isSpeaking) {
        if (flashTimer.current !== null) {
          window.clearTimeout(flashTimer.current);
          flashTimer.current = null;
        }
        modeRef.current = 'speaking';
        setReactorModeState('speaking');
      } else if (modeRef.current === 'speaking') {
        const next: ReactorMode = listeningRef.current ? 'listening' : 'idle';
        modeRef.current = next;
        setReactorModeState(next);
      }
    },
    onError: (message) => {
      logTranscript('system', message);
    },
    onUnsupported: () => {
      logTranscript(
        'system',
        'Voice recognition is not supported in this browser.',
      );
    },
  });

  const speak = useCallback(
    (text: string) => {
      logTranscript('jarvis', text);
      voice.speak(text);
      // If TTS is unsupported, still flare briefly so there is feedback.
      if (!voice.ttsSupported) flashMode('speaking', 1200);
    },
    [voice, logTranscript, flashMode],
  );

  const toggleListening = useCallback(() => {
    unlockAudio();
    if (voice.isListening()) {
      voice.stop();
      playSfx('blip');
    } else {
      playSfx('activation');
      voice.wakeUp(); // manual activation → awake, 60 s window
      voice.start();
    }
  }, [voice]);

  // Reactor spectrum: live when listening or speaking.
  const spectrumRef = useSpectrum(
    reactorMode === 'listening' || reactorMode === 'speaking',
  );

  useEffect(
    () => () => {
      if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    },
    [],
  );

  const value = useMemo<JarvisContextValue>(
    () => ({
      voice,
      voiceSupported,
      ttsSupported,
      wakeMode,
      listening,
      speaking,
      toggleListening,
      reactorMode,
      flashMode,
      setReactorMode,
      spectrumRef,
      transcript,
      logTranscript,
      clearTranscript,
      sfxEnabled,
      setSfxEnabledState,
      speak,
      sendChat,
      registerChatSend,
      chatBusy,
      setChatBusy,
      activityTick,
      bumpActivity,
    }),
    [
      voice,
      voiceSupported,
      ttsSupported,
      wakeMode,
      listening,
      speaking,
      toggleListening,
      reactorMode,
      flashMode,
      setReactorMode,
      spectrumRef,
      transcript,
      logTranscript,
      clearTranscript,
      sfxEnabled,
      setSfxEnabledState,
      speak,
      sendChat,
      registerChatSend,
      chatBusy,
      activityTick,
      bumpActivity,
    ],
  );

  return <JarvisContext.Provider value={value}>{children}</JarvisContext.Provider>;
}

export { playSfx };
