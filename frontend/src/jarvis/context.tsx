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
import type { JarvisVoice, VoiceState, WakeMode } from '../voice/jarvisVoice';
import { playSfx, setSfxEnabled, unlockAudio, useSpectrum } from '../audio/sfx';
import { getBriefing } from '../api';
import type { ChatHistoryTurn, WorkflowRun, WorkflowStep } from '../api';

export interface TranscriptLine {
  id: number;
  role: 'user' | 'jarvis' | 'system';
  text: string;
  at: string;
}

/** API the chat input registers so chips/voice can prefill or focus it. */
export interface ChatInputApi {
  prefill: (text: string) => void;
  focus: () => void;
}

export interface SendChatOpts {
  /** True when the message came from the voice transcript (drives 'thinking'). */
  voice?: boolean;
}

interface JarvisContextValue {
  voice: JarvisVoice;
  voiceSupported: boolean;
  ttsSupported: boolean;
  wakeMode: WakeMode;
  listening: boolean;
  /** True while J.A.R.V.I.S. is speaking (TTS). */
  speaking: boolean;
  /** Composite voice UX state: idle|listening|recognizing|thinking|speaking|error. */
  voiceState: VoiceState;
  /** Human-readable message for the current voice error, if any. */
  voiceError: string | null;
  /** Toggle continuous voice listening (handles wake-up + reactor mode). */
  toggleListening: () => void;
  /** Clear a voice error and attempt to listen again. */
  retryVoice: () => void;
  /** Cancel any in-flight speech immediately. */
  stopSpeaking: () => void;
  reactorMode: ReactorMode;
  /** Temporarily override reactor mode; reverts after ms. */
  flashMode: (mode: ReactorMode, ms?: number) => void;
  /**
   * Legacy escape hatch: clears any active flash so the reactor falls back
   * to its derived mode. The mode itself is derived from real app state.
   */
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
  sendChat: (text: string, opts?: SendChatOpts) => void;
  registerChatSend: (fn: ((text: string) => void) | null) => void;
  chatBusy: boolean;
  setChatBusy: (b: boolean) => void;
  /** Bumped whenever a chat workflow completes (refreshes activity log). */
  activityTick: number;
  bumpActivity: () => void;
  /** App-level tab navigation (command/workflows/system/security/knowledge/tasks/voice). */
  activeTab: string;
  setActiveTab: (tab: string) => void;
  /** Prefill the chat input (does not send). */
  prefillChat: (text: string) => void;
  /** Focus the chat input. */
  focusChatInput: () => void;
  registerChatInputApi: (api: ChatInputApi | null) => void;
  /** Live steps of the currently running workflow (from the SSE stream). */
  activeSteps: WorkflowStep[];
  setActiveSteps: (steps: WorkflowStep[]) => void;
  /** The most recently completed workflow run (drives badges + quick actions). */
  lastRun: WorkflowRun | null;
  setLastRun: (run: WorkflowRun | null) => void;
  /** Conversation transcript sent to the backend as `history` (last 10 turns). */
  history: ChatHistoryTurn[];
  appendHistory: (role: ChatHistoryTurn['role'], content: string) => void;
  clearHistory: () => void;
}

const JarvisContext = createContext<JarvisContextValue | null>(null);

export function useJarvis(): JarvisContextValue {
  const ctx = useContext(JarvisContext);
  if (!ctx) throw new Error('useJarvis must be used inside JarvisProvider');
  return ctx;
}

let lineId = 0;
/** Module-level: the proactive briefing must fire exactly once per session. */
let briefingFired = false;

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
  const [engineState, setEngineState] = useState<VoiceState>('idle');
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<string>('command');
  const [activeSteps, setActiveSteps] = useState<WorkflowStep[]>([]);
  const [lastRun, setLastRun] = useState<WorkflowRun | null>(null);
  const [history, setHistory] = useState<ChatHistoryTurn[]>([]);

  const flashRef = useRef<ReactorMode | null>(null);
  const flashTimer = useRef<number | null>(null);
  const baseModeRef = useRef<ReactorMode>('idle');
  const chatSendRef = useRef<((text: string) => void) | null>(null);
  const chatInputApiRef = useRef<ChatInputApi | null>(null);
  const voiceSendRef = useRef(false);

  const clearFlash = useCallback(() => {
    if (flashTimer.current !== null) {
      window.clearTimeout(flashTimer.current);
      flashTimer.current = null;
    }
    flashRef.current = null;
  }, []);

  const flashMode = useCallback(
    (mode: ReactorMode, ms = 2000) => {
      clearFlash();
      flashRef.current = mode;
      setReactorModeState(mode);
      flashTimer.current = window.setTimeout(() => {
        flashTimer.current = null;
        flashRef.current = null;
        // Revert to the current derived base mode.
        setReactorModeState(baseModeRef.current);
      }, ms);
    },
    [clearFlash],
  );

  /**
   * Legacy API kept for call sites that just want "back to normal":
   * clears any transient flash so the derived mode takes over.
   */
  const setReactorMode = useCallback(
    (_mode: ReactorMode) => {
      clearFlash();
    },
    [clearFlash],
  );

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

  const sendChat = useCallback((text: string, opts?: SendChatOpts) => {
    voiceSendRef.current = opts?.voice === true;
    chatSendRef.current?.(text);
  }, []);

  const registerChatSend = useCallback(
    (fn: ((text: string) => void) | null) => {
      chatSendRef.current = fn;
    },
    [],
  );

  const registerChatInputApi = useCallback((api: ChatInputApi | null) => {
    chatInputApiRef.current = api;
  }, []);

  const prefillChat = useCallback((text: string) => {
    chatInputApiRef.current?.prefill(text);
  }, []);

  const focusChatInput = useCallback(() => {
    chatInputApiRef.current?.focus();
  }, []);

  const appendHistory = useCallback(
    (role: ChatHistoryTurn['role'], content: string) => {
      setHistory((h) => [...h, { role, content }].slice(-20));
    },
    [],
  );
  const clearHistory = useCallback(() => setHistory([]), []);

  // Composite voice UX state: thinking = a voice-originated message is in
  // flight (chat busy) until the response starts.
  const voiceState: VoiceState = useMemo(() => {
    if (engineState === 'speaking') return 'speaking';
    if (engineState === 'error') return 'error';
    if (chatBusy && voiceSendRef.current) return 'thinking';
    return engineState;
  }, [engineState, chatBusy]);

  // Reactor mode is DERIVED from real app state — never set imperatively.
  const baseMode: ReactorMode = useMemo(() => {
    if (engineState === 'speaking') return 'speaking';
    if (activeSteps.some((s) => s.status === 'RUNNING' && s.type === 'tool')) {
      return 'processing';
    }
    if (chatBusy) return 'thinking';
    if (engineState === 'listening' || engineState === 'recognizing') {
      return 'listening';
    }
    return 'idle';
  }, [engineState, activeSteps, chatBusy]);

  useEffect(() => {
    baseModeRef.current = baseMode;
    if (flashRef.current === null) setReactorModeState(baseMode);
  }, [baseMode]);

  const { voice, supported: voiceSupported, ttsSupported } = useJarvisVoice({
    onTranscript: (text, isFinal) => {
      if (!isFinal) return;
      // The registered chat sender logs the transcript line itself.
      sendChat(text, { voice: true });
    },
    onListeningChange: (isListening) => {
      setListening(isListening);
    },
    onModeChange: (mode) => {
      setWakeMode(mode);
      if (mode === 'awake') {
        playSfx('blip');
        flashMode('attention', 900);
        logTranscript('system', 'Wake word detected — J.A.R.V.I.S. awake.');
      }
    },
    onSpeakingChange: (isSpeaking) => {
      setSpeakingState(isSpeaking);
    },
    onVoiceStateChange: (state) => {
      setEngineState(state);
      if (state === 'error') {
        setVoiceError(voice.getVoiceError());
      } else if (state !== 'speaking') {
        setVoiceError(null);
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

  const stopSpeaking = useCallback(() => {
    voice.stopSpeaking();
  }, [voice]);

  const retryVoice = useCallback(() => {
    setVoiceError(null);
    unlockAudio();
    voice.retry();
  }, [voice]);

  const toggleListening = useCallback(() => {
    unlockAudio();
    if (voice.isListening()) {
      voice.stop();
      playSfx('blip');
    } else {
      setVoiceError(null);
      playSfx('activation');
      voice.wakeUp(); // manual activation → awake, 60 s window
      voice.start();
    }
  }, [voice]);

  // Reactor spectrum: live when listening or speaking.
  const spectrumRef = useSpectrum(
    reactorMode === 'listening' || reactorMode === 'speaking',
  );

  // Proactive briefing — once per session, silent skip on any failure.
  useEffect(() => {
    if (briefingFired) return;
    briefingFired = true;
    void (async () => {
      const b = await getBriefing();
      if (!b) return;
      const parts: string[] = [];
      if (b.pending_tasks > 0) {
        parts.push(
          `You have ${b.pending_tasks} pending task${b.pending_tasks === 1 ? '' : 's'} today, sir.`,
        );
      }
      const due = b.due_soon[0];
      if (due) parts.push(`Sir, you have a reminder due soon: ${due.text}.`);
      if (parts.length > 0) logTranscript('jarvis', parts.join(' '));
    })();
  }, [logTranscript]);

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
      voiceState,
      voiceError,
      toggleListening,
      retryVoice,
      stopSpeaking,
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
      activeTab,
      setActiveTab,
      prefillChat,
      focusChatInput,
      registerChatInputApi,
      activeSteps,
      setActiveSteps,
      lastRun,
      setLastRun,
      history,
      appendHistory,
      clearHistory,
    }),
    [
      voice,
      voiceSupported,
      ttsSupported,
      wakeMode,
      listening,
      speaking,
      voiceState,
      voiceError,
      toggleListening,
      retryVoice,
      stopSpeaking,
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
      activeTab,
      prefillChat,
      focusChatInput,
      registerChatInputApi,
      activeSteps,
      lastRun,
      history,
      appendHistory,
      clearHistory,
    ],
  );

  return <JarvisContext.Provider value={value}>{children}</JarvisContext.Provider>;
}

export { playSfx };
