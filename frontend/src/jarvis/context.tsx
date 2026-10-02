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
import { useJarvisVoice, VOICE_LANG_KEY, VOLUME_STORAGE_KEY } from '../voice/jarvisVoice';
import type {
  JarvisVoice,
  VoiceLangSetting,
  VoiceState,
  WakeMode,
} from '../voice/jarvisVoice';
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

/** Live phase of the streaming chat request, driven by real SSE events. */
export type StreamPhase =
  | 'idle'
  | 'thinking'
  | 'processing'
  | 'speaking'
  | 'awaiting'
  | 'done'
  | 'error';

export interface StreamActivityItem {
  id: number;
  label: string;
  done: boolean;
  failed: boolean;
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
  registerChatSend: (fn: ((text: string, opts?: SendChatOpts) => void) | null) => void;
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
  /** Voice language setting (recognition locale + TTS voice hint). */
  voiceLang: VoiceLangSetting;
  setVoiceLang: (v: VoiceLangSetting) => void;
  /** TTS volume 0..1. */
  volume: number;
  setVolume: (v: number) => void;
  /** Live phase of the streaming chat request (real SSE events). */
  streamPhase: StreamPhase;
  setStreamPhase: (p: StreamPhase) => void;
  /** Real stream events for the activity feed; cleared on each new request. */
  streamActivity: StreamActivityItem[];
  pushStreamActivity: (label: string) => void;
  markStreamActivityDone: () => void;
  markStreamActivityError: () => void;
  clearStreamActivity: () => void;
  /** Chat UI registers its in-flight stream aborter; barge-in calls abortStream. */
  registerStreamAbort: (fn: (() => void) | null) => void;
  abortStream: () => void;
  /** "Talk to Spidey" continuous conversation mode. */
  conversationMode: boolean;
  startConversation: () => void;
  endConversation: () => void;
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
/** Module-level id counter for stream activity items. */
let activityId = 0;

function readStoredVoiceLang(): VoiceLangSetting {
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

function readStoredVolume(): number {
  try {
    const raw = localStorage.getItem(VOLUME_STORAGE_KEY);
    if (raw === null) return 1;
    const n = parseFloat(raw);
    if (Number.isNaN(n)) return 1;
    return Math.min(1, Math.max(0, n));
  } catch {
    return 1;
  }
}

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
  const [voiceLang, setVoiceLangState] = useState<VoiceLangSetting>(() => readStoredVoiceLang());
  const [volume, setVolumeState] = useState<number>(() => readStoredVolume());
  const [streamPhase, setStreamPhase] = useState<StreamPhase>('idle');
  const [streamActivity, setStreamActivity] = useState<StreamActivityItem[]>([]);
  const [conversationMode, setConversationMode] = useState(false);

  const flashRef = useRef<ReactorMode | null>(null);
  const flashTimer = useRef<number | null>(null);
  const baseModeRef = useRef<ReactorMode>('idle');
  const chatSendRef = useRef<((text: string, opts?: SendChatOpts) => void) | null>(null);
  const chatInputApiRef = useRef<ChatInputApi | null>(null);
  const voiceSendRef = useRef(false);
  const streamAbortRef = useRef<(() => void) | null>(null);
  const conversationModeRef = useRef(false);
  const chatBusyRef = useRef(false);

  useEffect(() => {
    chatBusyRef.current = chatBusy;
  }, [chatBusy]);

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
    chatSendRef.current?.(text, opts);
  }, []);

  const registerChatSend = useCallback(
    (fn: ((text: string, opts?: SendChatOpts) => void) | null) => {
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

  // -- stream activity feed (real SSE events only) ---------------------------

  /** Push a new activity item; any still-running item is marked done first. */
  const pushStreamActivity = useCallback((label: string) => {
    const text = label.trim();
    if (!text) return;
    setStreamActivity((prev) => {
      const settled = prev.map((i) =>
        i.done || i.failed ? i : { ...i, done: true },
      );
      return [...settled, { id: activityId++, label: text, done: false, failed: false }];
    });
  }, []);

  const markStreamActivityDone = useCallback(() => {
    setStreamActivity((prev) =>
      prev.map((i) => (i.failed ? i : { ...i, done: true })),
    );
  }, []);

  const markStreamActivityError = useCallback(() => {
    setStreamActivity((prev) =>
      prev.map((i) => (i.done || i.failed ? i : { ...i, failed: true })),
    );
  }, []);

  const clearStreamActivity = useCallback(() => setStreamActivity([]), []);

  // -- in-flight stream abort (barge-in, end conversation, unmount) ----------

  const registerStreamAbort = useCallback((fn: (() => void) | null) => {
    streamAbortRef.current = fn;
  }, []);

  const abortStream = useCallback(() => {
    try {
      streamAbortRef.current?.();
    } catch {
      /* ignore */
    }
  }, []);

  // Composite voice UX state: thinking = a voice-originated message is in
  // flight (chat busy) until the response starts.
  const voiceState: VoiceState = useMemo(() => {
    if (engineState === 'speaking') return 'speaking';
    if (engineState === 'error') return 'error';
    if (chatBusy && voiceSendRef.current) return 'thinking';
    return engineState;
  }, [engineState, chatBusy]);

  // Reactor mode is DERIVED from real app state — never set imperatively.
  // Stream SSE events drive it: thinking → THINKING, tool_start → PROCESSING
  // (tool_complete back to THINKING), speaking → SPEAKING, done → SUCCESS
  // (then IDLE), error → ERROR, awaiting_confirmation → ATTENTION.
  const baseMode: ReactorMode = useMemo(() => {
    if (engineState === 'speaking') return 'speaking';
    if (streamPhase === 'processing') return 'processing';
    if (streamPhase === 'awaiting') return 'attention';
    if (streamPhase === 'speaking') return 'speaking';
    if (streamPhase === 'thinking') return 'thinking';
    if (streamPhase === 'done') return 'success';
    if (streamPhase === 'error') return 'error';
    if (activeSteps.some((s) => s.status === 'RUNNING' && s.type === 'tool')) {
      return 'processing';
    }
    if (chatBusy) return 'thinking';
    if (engineState === 'listening' || engineState === 'recognizing') {
      return 'listening';
    }
    return 'idle';
  }, [engineState, streamPhase, activeSteps, chatBusy]);

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
    onBargeIn: () => {
      // User spoke over J.A.R.V.I.S.: the in-flight chat stream is aborted
      // so the new utterance is processed fresh (TTS was already cancelled
      // by the engine).
      abortStream();
    },
    onTtsQueueDrained: () => {
      // Conversation loop: TTS finished and no stream is running — make sure
      // the mic is back open (recognition usually never stopped, but Chrome
      // can end it silently mid-stream).
      if (conversationModeRef.current && !chatBusyRef.current) {
        voice.ensureListening();
      }
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

  const setVoiceLang = useCallback(
    (v: VoiceLangSetting) => {
      setVoiceLangState(v);
      voice.setRecognitionLang(v);
    },
    [voice],
  );

  const setVolume = useCallback((v: number) => {
    const clamped = Math.min(1, Math.max(0, v));
    setVolumeState(clamped);
    try {
      localStorage.setItem(VOLUME_STORAGE_KEY, String(clamped));
    } catch {
      /* ignore */
    }
  }, []);

  /**
   * "Talk to Spidey" continuous mode: mic stays open and the loop runs
   * LISTENING → (final transcript) → PROCESSING (stream) → SPEAKING (TTS) →
   * LISTENING automatically. Recognition's own end-of-speech ends each turn;
   * barge-in aborts the stream mid-response.
   */
  const startConversation = useCallback(() => {
    if (!voiceSupported) {
      setVoiceError(
        'Voice recognition is not supported in this browser — type instead.',
      );
      return;
    }
    unlockAudio();
    setVoiceError(null);
    voice.setStayAwake(true);
    voice.wakeUp();
    voice.start();
    conversationModeRef.current = true;
    setConversationMode(true);
    playSfx('activation');
    logTranscript('system', 'Conversation mode on — talk to Spidey, sir.');
  }, [voice, voiceSupported, logTranscript]);

  /**
   * End conversation: stop recognition, cancel TTS + queue, abort the
   * in-flight stream, drop the recognizer so the mic is released (the Web
   * Speech API holds the mic internally — stopping recognition is the only
   * release path; we never acquire a MediaStream of our own).
   */
  const endConversation = useCallback(() => {
    conversationModeRef.current = false;
    setConversationMode(false);
    voice.setStayAwake(false);
    voice.sleep();
    voice.releaseMicrophone();
    voice.stopSpeaking();
    abortStream();
    setStreamPhase('idle');
    playSfx('blip');
    logTranscript('system', 'Conversation ended — mic released.');
  }, [voice, abortStream, logTranscript]);

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
      voiceLang,
      setVoiceLang,
      volume,
      setVolume,
      streamPhase,
      setStreamPhase,
      streamActivity,
      pushStreamActivity,
      markStreamActivityDone,
      markStreamActivityError,
      clearStreamActivity,
      registerStreamAbort,
      abortStream,
      conversationMode,
      startConversation,
      endConversation,
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
      voiceLang,
      setVoiceLang,
      volume,
      setVolume,
      streamPhase,
      streamActivity,
      pushStreamActivity,
      markStreamActivityDone,
      markStreamActivityError,
      clearStreamActivity,
      registerStreamAbort,
      abortStream,
      conversationMode,
      startConversation,
      endConversation,
    ],
  );

  return <JarvisContext.Provider value={value}>{children}</JarvisContext.Provider>;
}

export { playSfx };
