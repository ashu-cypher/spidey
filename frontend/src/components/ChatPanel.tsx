import { useCallback, useEffect, useRef, useState } from 'react';
import { streamChat, StreamEventError, StreamInterruptedError } from '../api';
import type {
  ChatHistoryTurn,
  ConfirmationPayload,
  StreamEventType,
  VoiceLangSetting,
} from '../api';
import { StreamSpeechTracker } from '../chat/streamSpeech';
import { QuickActions } from './QuickActions';
import { HudButton, HudChip, HudInput, HudPanel } from './hud';
import { useMew } from '../mew/context';
import type { SendChatOpts } from '../mew/context';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
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

const SUGGESTIONS = [
  'Hello Spidey.',
  'calculate 12 * 8',
  'remember that I am learning Python',
  'remind me to drink water tomorrow',
  'search the web for the James Webb telescope',
];

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

export function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmationPayload | null>(null);
  const [failure, setFailure] = useState<FailureCard | null>(null);
  const {
    registerChatSend,
    registerChatInputApi,
    setChatBusy,
    speak,
    ttsSupported,
    speaking,
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
    registerStreamAbort,
  } = useMew();

  const inputRef = useRef<HTMLInputElement | null>(null);
  const lastSentRef = useRef<string>('');
  const abortRef = useRef<AbortController | null>(null);
  const phaseTimer = useRef<number | null>(null);
  const sendSeq = useRef(0);
  const confirmShownRef = useRef(false);
  const historyRef = useRef<ChatHistoryTurn[]>(history);
  historyRef.current = history;
  // messagesRef mirrors `messages` so streaming updates can target an index
  // without stale closures.
  const messagesRef = useRef<ChatMessage[]>([]);

  const pushMessage = useCallback((m: ChatMessage): number => {
    const next = [...messagesRef.current, m];
    messagesRef.current = next;
    setMessages(next);
    return next.length - 1;
  }, []);

  const setMessageText = useCallback((idx: number, text: string) => {
    const next = messagesRef.current.slice();
    if (!next[idx]) return;
    next[idx] = { ...next[idx], text };
    messagesRef.current = next;
    setMessages(next);
  }, []);

  const removeMessage = useCallback((idx: number) => {
    const next = messagesRef.current.slice();
    if (idx < 0 || idx >= next.length) return;
    next.splice(idx, 1);
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

  async function send(text: string, opts?: SendOpts) {
    const message = text.trim();
    if (!message) return;
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
    clearStreamActivity();
    setStreamPhase('thinking');

    pushMessage({ role: 'user', text: message });
    appendHistory('user', message);
    const assistantIdx = pushMessage({ role: 'assistant', text: '' });
    setInput('');
    setBusy(true);
    setChatBusy(true);

    // Phrase-wise TTS only for voice-originated sends (voice transcript or
    // conversation mode). Typed chat stays silent unless the 🔊 button is used.
    const autoSpeak = opts?.voice === true || conversationMode;
    const tracker = autoSpeak
      ? new StreamSpeechTracker((t, lang) => voice.enqueueSpeech(t, { lang }))
      : null;
    const hintLang = ttsHintLang(voiceLang, message);
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
      removeMessage(assistantIdx);
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
          setMessageText(assistantIdx, displayText);
          tracker?.delta(t);
          break;
        }
        case 'done': {
          const result = typeof data.result === 'string' ? data.result : '';
          const finalText = result || displayText || '(no response)';
          setMessageText(assistantIdx, finalText);
          appendHistory('assistant', finalText);
          if (tracker) {
            tracker.ttsLang =
              typeof data.lang === 'string' ? data.lang : null;
            tracker.flush();
          }
          markStreamActivityDone();
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
      await streamChat(message, historyRef.current.slice(-20), voiceLang, {
        signal: controller.signal,
        confirmToken: opts?.confirmToken,
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

  function focusInput() {
    inputRef.current?.focus();
  }

  function newConversation() {
    // Close any live stream before resetting.
    cancelInFlight();
    messagesRef.current = [];
    setMessages([]);
    setConfirm(null);
    setFailure(null);
    setInput('');
    clearHistory();
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

  // Expose the input so quick-action chips can prefill/focus it.
  useEffect(() => {
    registerChatInputApi({
      prefill: (text: string) => {
        setInput(text);
        inputRef.current?.focus();
      },
      focus: () => inputRef.current?.focus(),
    });
    return () => registerChatInputApi(null);
  }, [registerChatInputApi]);

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

  return (
    <HudPanel
      title="Command console"
      className="flex flex-col"
      right={
        messages.length > 0 ? (
          <button
            type="button"
            onClick={newConversation}
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-gold/70 hover:text-gold"
            title="Clear messages and conversation history"
          >
            New conversation
          </button>
        ) : undefined
      }
    >
      <div className="mb-3 h-[380px] space-y-3 overflow-y-auto pr-1">
        {messages.length === 0 && (
          <p className="text-sm text-cyan-200/40">
            Ask Spidey anything. Watch the live activity feed light up in the
            core panel.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[85%] rounded-lg px-4 py-2 text-sm whitespace-pre-wrap ${
                m.role === 'user'
                  ? 'border border-accent/40 bg-accent/10 text-cyan-100 shadow-[0_0_16px_rgba(0,240,255,0.12)]'
                  : 'border border-accent/15 bg-carbon/60 text-cyan-100/90'
              }`}
            >
              {m.text}
              {m.role === 'assistant' && m.text && (
                <div className="mt-1.5 flex gap-1">
                  <button
                    type="button"
                    title={ttsSupported ? 'Read this reply aloud' : 'Voice not supported in this browser'}
                    disabled={!ttsSupported}
                    onClick={() => speak(m.text)}
                    className="rounded border border-accent/20 bg-accent/5 px-1.5 py-0.5 text-xs text-cyan-200/60 hover:border-accent/50 hover:text-accent disabled:opacity-40"
                  >
                    🔊
                  </button>
                  {speaking && (
                    <button
                      type="button"
                      title="Stop reading"
                      onClick={() => voice.stopSpeaking()}
                      className="rounded border border-crimson/40 bg-crimson/10 px-1.5 py-0.5 text-xs text-red-300 hover:bg-crimson/20"
                    >
                      ⏹
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
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
                <HudButton onClick={focusInput}>Type instead</HudButton>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        {SUGGESTIONS.map((s) => (
          <HudChip key={s} onClick={() => void send(s)} disabled={busy}>
            {s}
          </HudChip>
        ))}
      </div>

      {confirm && (
        <div className="mb-3 rounded-lg border border-gold/50 bg-gold/10 p-4">
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

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
        className="flex gap-2"
      >
        <HudInput
          inputRef={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Message Spidey…"
          className="flex-1"
          disabled={busy}
        />
        <HudButton type="submit" variant="primary" disabled={busy || !input.trim()}>
          Send
        </HudButton>
      </form>

      <QuickActions />
    </HudPanel>
  );
}
