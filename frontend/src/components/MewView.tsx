import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getWorkflow,
  streamChat,
  uploadAttachment,
  StreamEventError,
  StreamInterruptedError,
} from '../api';
import type {
  Attachment,
  ChatHistoryTurn,
  ConfirmationPayload,
  StreamEventType,
  VoiceLangSetting,
} from '../api';
import { StreamSpeechTracker } from '../chat/streamSpeech';
import { ArcReactor } from './ArcReactor';
import { Markdown } from './Markdown';
import { UniversalInput } from './UniversalInput';
import { QuickActions } from './QuickActions';
import { StatusPanel } from './StatusPanel';
import { HudButton, HudChip } from './hud';
import { useMew } from '../mew/context';
import type { SendChatOpts } from '../mew/context';
import type { VoiceState } from '../voice/mewVoice';

interface ChatMessage {
  id: number;
  role: 'user' | 'assistant' | 'system';
  text: string;
  /** Files attached to this user message (rendered as contextual chips). */
  attachments?: Attachment[];
  /** Real stream-activity labels collected while this reply streamed. */
  toolLines?: string[];
  /** True when the backend's memory_update step reported saved=true. */
  memoryUpdated?: boolean;
}

interface FailureCard {
  kind: 'run' | 'service';
  title: string;
  detail: string;
}

interface SendOpts {
  confirmToken?: string;
  voice?: boolean;
}

let messageId = 0;
const CONVERSATION_KEY = 'mew.conversationId';

/** Stable conversation id per conversation, persisted in localStorage. */
function loadConversationId(): string {
  try {
    const existing = localStorage.getItem(CONVERSATION_KEY);
    if (existing) return existing;
  } catch {
    /* fall through */
  }
  const fresh =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `local-${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
  try {
    localStorage.setItem(CONVERSATION_KEY, fresh);
  } catch {
    /* ignore */
  }
  return fresh;
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError';
}

/** TTS voice hint for the response before the backend reports its language. */
function ttsHintLang(
  setting: VoiceLangSetting,
  message: string,
): string | null {
  if (setting === 'hi' || setting === 'hinglish') return 'hi';
  if (setting === 'en') return 'en';
  // auto: follow the detected script of the request
  return /[\u0900-\u097F]/.test(message) ? 'hi' : null;
}

/** Friendly label for a stream `state` event (prefers the backend's label). */
function stateLabel(state: string, data: Record<string, unknown>): string {
  if (typeof data.label === 'string' && data.label.trim()) {
    return data.label.trim();
  }
  switch (state) {
    case 'thinking':
      return 'Understanding request';
    case 'tool_start': {
      const tool =
        typeof data.tool === 'string' && data.tool ? data.tool : 'tool';
      return `Running ${tool} tool`;
    }
    case 'tool_complete':
      return 'Tool finished';
    case 'speaking':
      return 'Generating response';
    case 'awaiting_confirmation':
      return 'Awaiting confirmation';
    default:
      return state;
  }
}

function WakePill() {
  const { voiceSupported, voiceState, wakeMode } = useMew();
  if (!voiceSupported)
    return <span className="hud-pill hud-pill-off">Voice unsupported</span>;
  if (voiceState === 'listening' || voiceState === 'recognizing')
    return (
      <span className="hud-pill hud-pill-on">
        <span className="inline-block h-2 w-2 rounded-full bg-emerald-400 hud-blink" />
        Listening
      </span>
    );
  if (voiceState === 'speaking')
    return <span className="hud-pill hud-pill-on">Speaking</span>;
  if (voiceState === 'thinking')
    return <span className="hud-pill hud-pill-on">Thinking</span>;
  if (wakeMode === 'awake')
    return <span className="hud-pill hud-pill-on">Awake — 60s window</span>;
  return <span className="hud-pill hud-pill-off">Standby — say “Mew”</span>;
}

/** Voice recognition failure card with recovery actions. */
function VoiceErrorCard() {
  const { voiceError, retryVoice, focusChatInput, chatBusy } = useMew();
  if (!voiceError) return null;
  return (
    <div className="mt-3 w-full rounded-lg border border-crimson/40 bg-crimson/10 px-4 py-3">
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-red-300">
        I couldn&apos;t hear that clearly.
      </p>
      <p className="mt-1 text-xs text-cyan-100/60">{voiceError}</p>
      <div className="mt-2 flex gap-2">
        <HudChip onClick={retryVoice} disabled={chatBusy} title="Try listening again">
          Try again
        </HudChip>
        <HudChip onClick={focusChatInput} title="Type your message instead">
          Type instead
        </HudChip>
      </div>
    </div>
  );
}

/** "Talk to MEW" continuous conversation mode toggle. */
function ConversationToggle() {
  const {
    conversationMode,
    startConversation,
    endConversation,
    voiceSupported,
    chatBusy,
  } = useMew();

  if (!voiceSupported) return null;

  if (conversationMode) {
    return (
      <button
        type="button"
        onClick={endConversation}
        className="rounded-lg border border-crimson/70 bg-crimson/15 px-4 py-1.5 font-mono text-[11px] uppercase tracking-[0.2em] text-red-300 shadow-[0_0_24px_rgba(239,68,68,0.35)] hover:bg-crimson/25"
        title="Stop recognition, cancel speech, release the mic"
      >
        ⏹ End conversation
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={startConversation}
      disabled={chatBusy}
      className="rounded-lg border border-accent/70 bg-accent/10 px-4 py-1.5 font-mono text-[11px] uppercase tracking-[0.2em] text-accent shadow-[0_0_24px_rgba(0,240,255,0.3)] hover:bg-accent/20 disabled:opacity-40"
      title="Keep the mic open: talk, MEW answers aloud, repeat"
    >
      🗣 Talk to MEW
    </button>
  );
}

function VoiceStateLabel({ state }: { state: VoiceState }) {
  if (state === 'listening' || state === 'recognizing')
    return (
      <span className="font-mono text-[11px] uppercase tracking-[0.25em] text-accent hud-blink">
        Listening…
      </span>
    );
  return null;
}

interface MessageActionsProps {
  message: ChatMessage;
  isLastAssistant: boolean;
  onRegenerate: () => void;
}

function MessageActions({ message, isLastAssistant, onRegenerate }: MessageActionsProps) {
  const { speak, ttsSupported, speaking, voice, chatBusy } = useMew();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable */
    }
  };

  const btn =
    'rounded border border-accent/20 bg-accent/5 px-1.5 py-0.5 text-xs text-cyan-200/60 hover:border-accent/50 hover:text-accent disabled:opacity-40';

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1">
      <button type="button" title="Copy message" onClick={copy} className={btn}>
        {copied ? '✓' : '⧉'}
      </button>
      {message.role === 'assistant' && (
        <button
          type="button"
          title={ttsSupported ? 'Read this reply aloud' : 'Voice not supported in this browser'}
          disabled={!ttsSupported}
          onClick={() => speak(message.text)}
          className={btn}
        >
          🔊
        </button>
      )}
      {message.role === 'assistant' && speaking && (
        <button
          type="button"
          title="Stop reading"
          onClick={() => voice.stopSpeaking()}
          className="rounded border border-crimson/40 bg-crimson/10 px-1.5 py-0.5 text-xs text-red-300 hover:bg-crimson/20"
        >
          ⏹
        </button>
      )}
      {message.role === 'assistant' && isLastAssistant && (
        <button
          type="button"
          title="Regenerate — re-send the last message"
          onClick={onRegenerate}
          disabled={chatBusy}
          className={btn}
        >
          ↻ Regenerate
        </button>
      )}
    </div>
  );
}

function AttachmentChips({ attachments }: { attachments: Attachment[] }) {
  return (
    <div className="mb-1.5 flex flex-wrap gap-1.5">
      {attachments.map((a) => (
        <span
          key={a.id}
          title={`${a.kind} · attached ${a.created_at ?? ''}`}
          className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-gold/40 bg-gold/10 px-2.5 py-0.5 text-[11px] text-gold"
        >
          <span aria-hidden="true">📎</span>
          <span className="max-w-[200px] truncate font-medium">{a.filename}</span>
        </span>
      ))}
    </div>
  );
}

interface MessageBubbleProps {
  message: ChatMessage;
  isLastAssistant: boolean;
  onRegenerate: () => void;
}

function MessageBubble({ message, isLastAssistant, onRegenerate }: MessageBubbleProps) {
  if (message.role === 'system') {
    return (
      <div className="flex justify-center">
        <p className="max-w-[90%] rounded-full border border-accent/20 bg-carbon/60 px-4 py-1.5 text-center text-xs text-cyan-200/60">
          {message.text}
        </p>
      </div>
    );
  }

  const isUser = message.role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[88%] rounded-2xl px-4 py-2.5 md:max-w-[82%] ${
          isUser
            ? 'rounded-br-md border border-accent/40 bg-accent/10 text-cyan-100 shadow-[0_0_16px_rgba(0,240,255,0.12)]'
            : 'rounded-bl-md border border-accent/15 bg-carbon/60 text-cyan-100/90'
        }`}
      >
        {isUser && message.attachments && message.attachments.length > 0 && (
          <AttachmentChips attachments={message.attachments} />
        )}
        {isUser ? (
          <p className="whitespace-pre-wrap text-sm">{message.text}</p>
        ) : (
          <Markdown text={message.text || '…'} />
        )}
        {!isUser && message.toolLines && message.toolLines.length > 0 && (
          <p className="mt-1.5 border-t border-accent/10 pt-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-cyan-200/40">
            ⚙ {message.toolLines.join(' · ')}
          </p>
        )}
        {!isUser && message.memoryUpdated && (
          <p className="mt-1.5 inline-flex items-center gap-1.5 rounded-full border border-violet-400/40 bg-violet-400/10 px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em] text-violet-300">
            🧠 Memory updated
          </p>
        )}
        <MessageActions
          message={message}
          isLastAssistant={isLastAssistant}
          onRegenerate={onRegenerate}
        />
      </div>
    </div>
  );
}

export function MewView() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [pending, setPending] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmationPayload | null>(null);
  const [failure, setFailure] = useState<FailureCard | null>(null);
  const [dragActive, setDragActive] = useState(false);

  const {
    registerChatSend,
    setChatBusy,
    voice,
    logTranscript,
    bumpActivity,
    history,
    appendHistory,
    clearHistory,
    voiceLang,
    conversationMode,
    setStreamPhase,
    pushStreamActivity,
    markStreamActivityDone,
    markStreamActivityError,
    clearStreamActivity,
    streamActivity,
    registerStreamAbort,
    reactorMode,
    spectrumRef,
    voiceState,
    notices,
    dismissNotice,
  } = useMew();

  const conversationIdRef = useRef<string>(loadConversationId());
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);
  const dragDepth = useRef(0);
  const lastSentRef = useRef<string>('');
  const abortRef = useRef<AbortController | null>(null);
  const phaseTimer = useRef<number | null>(null);
  const sendSeq = useRef(0);
  const confirmShownRef = useRef(false);
  const historyRef = useRef<ChatHistoryTurn[]>(history);
  historyRef.current = history;
  const activityRef = useRef(streamActivity);
  activityRef.current = streamActivity;
  const sawMemoryUpdateRef = useRef(false);
  // messagesRef mirrors `messages` so streaming updates can target an id
  // without stale closures.
  const messagesRef = useRef<ChatMessage[]>([]);

  const pushMessage = useCallback((m: Omit<ChatMessage, 'id'>): number => {
    const full: ChatMessage = { ...m, id: messageId++ };
    const next = [...messagesRef.current, full];
    messagesRef.current = next;
    setMessages(next);
    return full.id;
  }, []);

  const patchMessage = useCallback(
    (id: number, patch: Partial<ChatMessage>) => {
      const next = messagesRef.current.map((m) =>
        m.id === id ? { ...m, ...patch } : m,
      );
      messagesRef.current = next;
      setMessages(next);
    },
    [],
  );

  const removeMessage = useCallback((id: number) => {
    const next = messagesRef.current.filter((m) => m.id !== id);
    messagesRef.current = next;
    setMessages(next);
  }, []);

  const schedulePhaseIdle = useCallback(
    (ms: number) => {
      if (phaseTimer.current !== null) {
        window.clearTimeout(phaseTimer.current);
      }
      phaseTimer.current = window.setTimeout(() => {
        phaseTimer.current = null;
        setStreamPhase('idle');
      }, ms);
    },
    [setStreamPhase],
  );

  /**
   * Tear down the in-flight stream: invalidate its sequence so late events
   * and its finally-block are ignored, abort the fetch, clear timers, and
   * release the busy lock. Safe to call when idle. Registered for barge-in.
   */
  const cancelInFlight = useCallback(() => {
    sendSeq.current += 1;
    if (phaseTimer.current !== null) {
      window.clearTimeout(phaseTimer.current);
      phaseTimer.current = null;
    }
    const c = abortRef.current;
    abortRef.current = null;
    try {
      c?.abort();
    } catch {
      /* ignore */
    }
    registerStreamAbort(null);
    setBusy(false);
    setChatBusy(false);
    setStreamPhase('idle');
  }, [registerStreamAbort, setChatBusy, setStreamPhase]);

  const addFiles = useCallback((files: File[]) => {
    if (files.length === 0) return;
    setPending((prev) => [...prev, ...files].slice(0, 10));
  }, []);

  const checkMemorySaved = useCallback(
    (runId: string, assistantId: number, seq: number) => {
      // Honest "Memory updated" pill: only when the run's memory_update step
      // reports saved=true in its output (backend contract).
      void getWorkflow(runId)
        .then((run) => {
          if (sendSeq.current !== seq) return;
          const saved = run.steps.some(
            (s) =>
              s.type === 'memory_update' &&
              (s.output as { saved?: unknown } | null)?.saved === true,
          );
          if (saved) patchMessage(assistantId, { memoryUpdated: true });
        })
        .catch(() => {
          /* backend unreachable — no pill, no error */
        });
    },
    [patchMessage],
  );

  async function send(text: string, opts?: SendOpts) {
    const message = text.trim();
    const files = pending;
    if (!message && files.length === 0) return;
    if (busy && !opts?.voice) return; // typed input waits its turn
    // A voice barge-in preempts the in-flight stream; otherwise this is a
    // no-op that guarantees clean state.
    cancelInFlight();
    const seq = ++sendSeq.current;

    // A new message always cancels in-flight speech first.
    voice.stopSpeaking();
    lastSentRef.current = message;
    setFailure(null);
    setConfirm(null);
    confirmShownRef.current = false;
    sawMemoryUpdateRef.current = false;
    clearStreamActivity();
    setStreamPhase('thinking');
    setPending([]);

    // 1) Upload attachments first so the agent sees them with this turn.
    const attached: Attachment[] = [];
    for (const f of files) {
      try {
        const a = await uploadAttachment(
          f,
          conversationIdRef.current,
          message || undefined,
        );
        attached.push(a);
        pushMessage({
          role: 'system',
          text: `Attached ${a.filename} — MEW can now answer questions about it.`,
        });
      } catch (err: unknown) {
        if (sendSeq.current !== seq) return; // superseded mid-upload
        pushMessage({
          role: 'system',
          text: `Couldn't attach ${f.name} (${err instanceof Error ? err.message : 'upload failed'}). The message was still sent.`,
        });
      }
    }
    if (sendSeq.current !== seq) return;

    const userText =
      message || (attached.length > 0 ? `(sent ${attached.length} attachment${attached.length === 1 ? '' : 's'})` : '');
    if (!userText && attached.length === 0) return;

    pushMessage({
      role: 'user',
      text: userText,
      attachments: attached.length > 0 ? attached : undefined,
    });
    appendHistory(
      'user',
      attached.length > 0
        ? `${userText} [attached: ${attached.map((a) => a.filename).join(', ')}]`
        : userText,
    );
    const assistantId = pushMessage({ role: 'assistant', text: '' });
    setInput('');
    setBusy(true);
    setChatBusy(true);

    // Phrase-wise TTS only for voice-originated sends (voice transcript or
    // conversation mode). Typed chat stays silent unless the 🔊 button is used.
    const autoSpeak = opts?.voice === true || conversationMode;
    const tracker = autoSpeak
      ? new StreamSpeechTracker((t, lang) => voice.enqueueSpeech(t, { lang }))
      : null;
    const hintLang = ttsHintLang(voiceLang, userText);
    let displayText = '';

    const controller = new AbortController();
    abortRef.current = controller;

    const showConfirmation = (proposal: string, token: string) => {
      if (confirmShownRef.current || !token) return;
      confirmShownRef.current = true;
      setConfirm({ needs_confirmation: true, proposal, confirm_token: token });
      setStreamPhase('awaiting');
      pushStreamActivity('Awaiting confirmation');
      const proposalText = `Needs your confirmation: ${proposal}`;
      pushMessage({ role: 'assistant', text: proposalText });
      appendHistory('assistant', proposalText);
    };

    const failStream = (
      kind: FailureCard['kind'],
      title: string,
      detail: string,
    ) => {
      // Drop the empty streaming placeholder so no hollow bubble remains.
      removeMessage(assistantId);
      markStreamActivityError();
      setStreamPhase('error');
      schedulePhaseIdle(2200);
      setFailure({ kind, title, detail });
      if (autoSpeak) voice.speak('Sorry sir, something went wrong.');
    };

    const onEvent = (type: StreamEventType, data: Record<string, unknown>) => {
      if (sendSeq.current !== seq) return; // superseded by a newer send
      switch (type) {
        case 'state': {
          const st = typeof data.state === 'string' ? data.state : '';
          const label = stateLabel(st, data);
          if (/updating memory/i.test(label)) sawMemoryUpdateRef.current = true;
          switch (st) {
            case 'thinking':
              setStreamPhase('thinking');
              pushStreamActivity(label);
              break;
            case 'tool_start':
              setStreamPhase('processing');
              pushStreamActivity(label);
              break;
            case 'tool_complete':
              setStreamPhase('thinking');
              pushStreamActivity(label);
              break;
            case 'speaking':
              setStreamPhase('speaking');
              pushStreamActivity(label);
              break;
            case 'awaiting_confirmation': {
              const proposal =
                typeof data.proposal === 'string' ? data.proposal : '';
              const token =
                typeof data.confirm_token === 'string'
                  ? data.confirm_token
                  : '';
              pushStreamActivity(label);
              if (proposal && token) showConfirmation(proposal, token);
              else setStreamPhase('awaiting');
              break;
            }
            case 'done':
            case 'error':
              break; // terminal payloads arrive as their own events
            default:
              if (label) pushStreamActivity(label);
              break;
          }
          break;
        }
        case 'voice_summary': {
          const t = typeof data.text === 'string' ? data.text : '';
          if (t) tracker?.summary(t, hintLang);
          break;
        }
        case 'delta': {
          const t = typeof data.text === 'string' ? data.text : '';
          if (!t) break;
          displayText += t;
          patchMessage(assistantId, { text: displayText });
          tracker?.delta(t);
          break;
        }
        case 'done': {
          const result = typeof data.result === 'string' ? data.result : '';
          const finalText = result || displayText || '(no response)';
          // Contextual tool-activity line from this reply's real events.
          const toolLines = activityRef.current
            .map((i) => i.label)
            .filter((l, idx, arr) => l && arr.indexOf(l) === idx)
            .slice(0, 8);
          patchMessage(assistantId, {
            text: finalText,
            toolLines: toolLines.length > 0 ? toolLines : undefined,
          });
          appendHistory('assistant', finalText);
          if (tracker) {
            tracker.ttsLang =
              typeof data.lang === 'string' ? data.lang : null;
            tracker.flush();
          }
          markStreamActivityDone();
          if (sawMemoryUpdateRef.current) {
            const runId =
              typeof data.run_id === 'string' ? data.run_id : '';
            if (runId) checkMemorySaved(runId, assistantId, seq);
          }
          const needsConfirm = data.needs_confirmation === true;
          const token =
            typeof data.confirm_token === 'string' ? data.confirm_token : '';
          const proposal =
            typeof data.proposal === 'string' ? data.proposal : '';
          if (needsConfirm && token) {
            showConfirmation(proposal || finalText, token);
          } else {
            setStreamPhase('done');
            schedulePhaseIdle(1600);
          }
          break;
        }
        case 'error': {
          const message =
            typeof data.message === 'string' && data.message
              ? data.message
              : 'Something went wrong on my end, sir.';
          failStream('run', 'Something went wrong.', message);
          break;
        }
      }
    };

    try {
      await streamChat(userText, historyRef.current.slice(-20), voiceLang, {
        signal: controller.signal,
        confirmToken: opts?.confirmToken,
        conversationId: conversationIdRef.current,
        onEvent,
      });
    } catch (err) {
      if (sendSeq.current !== seq) return;
      if (isAbortError(err)) return; // barge-in / new send / unmount — silent
      if (err instanceof StreamEventError) return; // error card already shown
      if (err instanceof StreamInterruptedError) {
        failStream(
          'run',
          'Something went wrong.',
          'The live stream was interrupted.',
        );
      } else {
        failStream(
          'service',
          "I'm having trouble reaching my AI service.",
          'Check that the backend is running, then retry.',
        );
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      if (sendSeq.current === seq) {
        setBusy(false);
        setChatBusy(false);
        bumpActivity();
      }
    }
  }

  function retryLast() {
    if (lastSentRef.current) void send(lastSentRef.current);
  }

  function newConversation() {
    // Close any live stream before resetting; rotate the conversation id so
    // future attachments start a fresh server-side context.
    cancelInFlight();
    messagesRef.current = [];
    setMessages([]);
    setConfirm(null);
    setFailure(null);
    setInput('');
    setPending([]);
    clearHistory();
    const fresh =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `local-${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
    conversationIdRef.current = fresh;
    try {
      localStorage.setItem(CONVERSATION_KEY, fresh);
    } catch {
      /* ignore */
    }
    logTranscript('system', 'Conversation cleared — starting fresh.');
  }

  // Keep a fresh ref to send so chips/voice always call the latest closure.
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    registerChatSend((text: string, opts?: SendChatOpts) => {
      logTranscript('user', text);
      void sendRef.current(text, { voice: opts?.voice });
    });
    return () => registerChatSend(null);
  }, [registerChatSend, logTranscript]);

  // Barge-in / end-conversation abort the in-flight stream via the context.
  useEffect(() => {
    registerStreamAbort(() => cancelInFlight());
    return () => registerStreamAbort(null);
  }, [registerStreamAbort, cancelInFlight]);

  // Reactor reflects chat processing state (derived in context).
  useEffect(() => {
    setChatBusy(busy);
  }, [busy, setChatBusy]);

  useEffect(
    () => () => {
      try {
        abortRef.current?.abort();
      } catch {
        /* ignore */
      }
      if (phaseTimer.current !== null) window.clearTimeout(phaseTimer.current);
    },
    [],
  );

  // Sticky auto-scroll: follow new content only when already near the bottom.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages, busy]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90;
  };

  // Drag & drop anywhere over the conversation.
  const onDragEnter = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    dragDepth.current += 1;
    setDragActive(true);
  };
  const onDragOver = (e: React.DragEvent) => {
    if (dragActive) e.preventDefault();
  };
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragActive(false);
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    dragDepth.current = 0;
    setDragActive(false);
    const files = Array.from(e.dataTransfer.files ?? []);
    if (files.length > 0) addFiles(files);
  };

  async function approveConfirm() {
    if (!confirm || busy) return;
    const { confirm_token: token, proposal } = confirm;
    setConfirm(null);
    await send(`Confirmed: ${proposal}`, { confirmToken: token });
  }

  function cancelConfirm() {
    setConfirm(null);
    setStreamPhase('idle');
    pushMessage({ role: 'assistant', text: 'Cancelled — nothing was changed.' });
  }

  const chatting = messages.length > 0;
  const lastAssistantId = [...messagesRef.current]
    .reverse()
    .find((m) => m.role === 'assistant' && m.text)?.id;

  return (
    <div
      className="relative mx-auto flex max-w-3xl flex-col"
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {dragActive && (
        <div className="pointer-events-none absolute inset-0 z-30 flex items-center justify-center rounded-2xl border-2 border-dashed border-accent/70 bg-carbon/80 backdrop-blur-sm">
          <p className="font-mono text-sm uppercase tracking-[0.25em] text-accent">
            📎 Drop files to attach
          </p>
        </div>
      )}

      {/* Reactor — smoothly shrinks once the conversation starts. */}
      <div
        className="flex justify-center overflow-hidden transition-[height] duration-500 ease-in-out"
        style={{ height: chatting ? 124 : 320 }}
        aria-hidden={chatting}
      >
        <div
          className="transition-transform duration-500 ease-in-out"
          style={{
            transform: `scale(${chatting ? 0.375 : 1})`,
            transformOrigin: 'top center',
            width: 320,
            height: 320,
          }}
        >
          <div className="hud-flicker">
            <ArcReactor mode={reactorMode} spectrum={spectrumRef} size={320} />
          </div>
        </div>
      </div>

      {/* Notices (reminders, protocols, proactive briefing). */}
      {notices.length > 0 && (
        <div className="mb-3 space-y-2">
          {notices.map((n) => (
            <div
              key={n.id}
              className="flex items-start gap-3 rounded-xl border border-gold/40 bg-gold/10 px-4 py-2.5"
            >
              <span aria-hidden="true" className="mt-0.5">🔔</span>
              <p className="flex-1 text-sm text-gold">{n.text}</p>
              <button
                type="button"
                onClick={() => dismissNotice(n.id)}
                title="Dismiss"
                aria-label="Dismiss notice"
                className="text-gold/60 hover:text-gold"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {!chatting ? (
        <div className="mb-6 text-center">
          <h2 className="glow font-mono text-xl font-bold tracking-[0.12em] text-white md:text-2xl">
            What are we working on?
          </h2>
          <p className="mt-2 text-sm text-cyan-200/50">
            Ask me anything — or attach a file and I&apos;ll work with it.
          </p>
        </div>
      ) : (
        <div className="mb-3 flex items-center justify-between">
          <p className="font-mono text-[11px] uppercase tracking-[0.25em] text-cyan-200/40">
            Conversation
          </p>
          <button
            type="button"
            onClick={newConversation}
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-gold/70 hover:text-gold"
            title="Clear messages and start a fresh conversation"
          >
            New conversation
          </button>
        </div>
      )}

      {chatting && (
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="mb-4 max-h-[52vh] space-y-3 overflow-y-auto pr-1"
        >
          {messages.map((m) => (
            <MessageBubble
              key={m.id}
              message={m}
              isLastAssistant={m.id === lastAssistantId}
              onRegenerate={retryLast}
            />
          ))}
          {busy && (
            <div className="flex justify-start">
              <div className="hud-blink rounded-lg border border-accent/20 bg-accent/5 px-4 py-2 font-mono text-xs uppercase tracking-[0.25em] text-accent">
                Processing…
              </div>
            </div>
          )}
          {failure && (
            <div className="flex justify-start">
              <div className="max-w-[85%] rounded-lg border border-crimson/40 bg-crimson/10 px-4 py-3">
                <p className="font-mono text-xs uppercase tracking-[0.2em] text-red-300">
                  {failure.title}
                </p>
                <p className="mt-1 text-sm text-cyan-100/70">{failure.detail}</p>
                <div className="mt-2 flex gap-2">
                  <HudButton
                    variant="danger"
                    onClick={retryLast}
                    disabled={busy || !lastSentRef.current}
                  >
                    Try again
                  </HudButton>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {confirm && (
        <div className="mb-3 rounded-2xl border border-gold/50 bg-gold/10 p-4">
          <p className="mb-1 font-mono text-xs uppercase tracking-[0.25em] text-gold">
            Confirmation needed
          </p>
          <p className="mb-3 text-sm text-cyan-100/90">{confirm.proposal}</p>
          <div className="flex gap-2">
            <HudButton variant="gold" onClick={() => void approveConfirm()} disabled={busy}>
              Confirm
            </HudButton>
            <HudButton onClick={cancelConfirm}>Cancel</HudButton>
          </div>
        </div>
      )}

      <UniversalInput
        value={input}
        onChange={setInput}
        onSend={() => void send(input)}
        pending={pending}
        onFiles={addFiles}
        onRemovePending={(i) =>
          setPending((prev) => prev.filter((_, idx) => idx !== i))
        }
        busy={busy}
        onStop={cancelInFlight}
      />

      <div className="mt-3 flex flex-wrap items-center justify-center gap-3">
        <ConversationToggle />
        <WakePill />
        <VoiceStateLabel state={voiceState} />
      </div>
      <VoiceErrorCard />

      <QuickActions />

      <div className="mt-4">
        <StatusPanel />
      </div>
    </div>
  );
}
