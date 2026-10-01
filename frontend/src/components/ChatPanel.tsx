import { useEffect, useRef, useState } from 'react';
import { postChat } from '../api';
import type {
  ChatHistoryTurn,
  ConfirmationPayload,
  WorkflowRun,
  WorkflowStep,
} from '../api';
import { WorkflowPanel } from './WorkflowPanel';
import { QuickActions } from './QuickActions';
import { HudButton, HudChip, HudInput, HudPanel, HudTitle } from './hud';
import { useJarvis } from '../jarvis/context';
import { memoryWasSaved } from '../jarvis/steps';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

interface FailureCard {
  kind: 'run' | 'service';
  title: string;
  detail: string;
}

const SUGGESTIONS = [
  'Hello Spidey.',
  'calculate 12 * 8',
  'remember that I am learning Python',
  'remind me to drink water tomorrow',
  'search the web for the James Webb telescope',
];

function parseConfirmation(result: string | null): ConfirmationPayload | null {
  if (!result) return null;
  try {
    const data = JSON.parse(result) as ConfirmationPayload;
    if (data && data.needs_confirmation && data.confirm_token) return data;
  } catch {
    // Not a confirmation payload — plain assistant text.
  }
  return null;
}

function upsert(steps: WorkflowStep[], step: WorkflowStep): WorkflowStep[] {
  const idx = steps.findIndex((s) => s.step_id === step.step_id);
  if (idx === -1) return [...steps, step];
  const next = steps.slice();
  next[idx] = step;
  return next;
}

export function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [liveSteps, setLiveSteps] = useState<WorkflowStep[]>([]);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmationPayload | null>(null);
  const [failure, setFailure] = useState<FailureCard | null>(null);
  const [memoryBadge, setMemoryBadge] = useState(false);
  const {
    registerChatSend,
    registerChatInputApi,
    setChatBusy,
    flashMode,
    speak,
    ttsSupported,
    speaking,
    voice,
    logTranscript,
    bumpActivity,
    setActiveSteps,
    setLastRun,
    history,
    appendHistory,
    clearHistory,
  } = useJarvis();

  const inputRef = useRef<HTMLInputElement | null>(null);
  const lastSentRef = useRef<string>('');
  const memoryTimer = useRef<number | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const historyRef = useRef<ChatHistoryTurn[]>(history);
  historyRef.current = history;
  const liveStepsRef = useRef<WorkflowStep[]>([]);
  liveStepsRef.current = liveSteps;

  async function send(text: string, confirmToken?: string) {
    const message = text.trim();
    if (!message || busy) return;
    // A new message always cancels in-flight speech first.
    voice.stopSpeaking();
    lastSentRef.current = message;
    setFailure(null);
    setMessages((m) => [...m, { role: 'user', text: message }]);
    appendHistory('user', message);
    setInput('');
    setBusy(true);
    setLiveSteps([]);
    liveStepsRef.current = [];
    setActiveSteps([]);
    setLastRun(null);
    try {
      const { run_id } = await postChat(
        message,
        confirmToken,
        historyRef.current, // capped at the last 10 turns (20 entries)
      );
      const es = new EventSource(
        `/api/workflow/${encodeURIComponent(run_id)}/stream`,
      );
      esRef.current = es;
      es.addEventListener('step_update', (e: Event) => {
        const step = JSON.parse((e as MessageEvent).data) as WorkflowStep;
        const next = upsert(liveStepsRef.current, step);
        liveStepsRef.current = next;
        setLiveSteps(next);
        setActiveSteps(next);
      });
      es.addEventListener('done', (e: Event) => {
        const run = JSON.parse((e as MessageEvent).data) as WorkflowRun;
        esRef.current = null;
        setLiveSteps(run.steps);
        setActiveSteps([]);
        setLastRun(run);
        if (run.status === 'failed') {
          flashMode('error', 2000);
          setFailure({
            kind: 'run',
            title: 'Something went wrong.',
            detail: 'The workflow failed before it could finish.',
          });
        } else {
          flashMode('success', 1200);
          if (memoryWasSaved(run.steps)) {
            setMemoryBadge(true);
            if (memoryTimer.current !== null) {
              window.clearTimeout(memoryTimer.current);
            }
            memoryTimer.current = window.setTimeout(() => {
              memoryTimer.current = null;
              setMemoryBadge(false);
            }, 4000);
          }
        }
        const payload = parseConfirmation(run.result);
        if (payload) {
          setConfirm(payload);
          const proposalText = `Needs your confirmation: ${payload.proposal}`;
          setMessages((m) => [...m, { role: 'assistant', text: proposalText }]);
          appendHistory('assistant', proposalText);
        } else {
          const reply = run.result ?? '(no response)';
          setMessages((m) => [...m, { role: 'assistant', text: reply }]);
          appendHistory('assistant', reply);
        }
        es.close();
        setBusy(false);
        bumpActivity();
      });
      es.onerror = () => {
        esRef.current = null;
        es.close();
        setActiveSteps([]);
        setBusy(false);
        setFailure({
          kind: 'run',
          title: 'Something went wrong.',
          detail: 'The live workflow stream was interrupted.',
        });
      };
    } catch {
      setBusy(false);
      setFailure({
        kind: 'service',
        title: "I'm having trouble reaching my AI service.",
        detail: 'Check that the backend is running, then retry.',
      });
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
    esRef.current?.close();
    esRef.current = null;
    setMessages([]);
    setLiveSteps([]);
    liveStepsRef.current = [];
    setActiveSteps([]);
    setLastRun(null);
    setConfirm(null);
    setFailure(null);
    setMemoryBadge(false);
    setInput('');
    clearHistory();
    logTranscript('system', 'Conversation cleared — starting fresh.');
  }

  // Keep a fresh ref to send so chips/voice always call the latest closure.
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    registerChatSend((text: string) => {
      logTranscript('user', text);
      void sendRef.current(text);
    });
    return () => registerChatSend(null);
  }, [registerChatSend, logTranscript]);

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
      esRef.current?.close();
      if (memoryTimer.current !== null) window.clearTimeout(memoryTimer.current);
    },
    [],
  );

  async function approveConfirm() {
    if (!confirm || busy) return;
    const { confirm_token: token, proposal } = confirm;
    setConfirm(null);
    await send(`Confirmed: ${proposal}`, token);
  }

  function cancelConfirm() {
    setConfirm(null);
    setMessages((m) => [
      ...m,
      { role: 'assistant', text: 'Cancelled — nothing was changed.' },
    ]);
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
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
              Ask Spidey anything. Watch the live workflow light up on the right.
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
                {m.role === 'assistant' && (
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

        {memoryBadge && (
          <div className="mb-2 flex">
            <span className="hud-fadein rounded-full border border-gold/50 bg-gold/10 px-3 py-1 font-mono text-[10px] uppercase tracking-[0.2em] text-gold">
              🧠 Memory updated
            </span>
          </div>
        )}

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
          />
          <HudButton type="submit" variant="primary" disabled={busy || !input.trim()}>
            Send
          </HudButton>
        </form>

        <QuickActions />
      </HudPanel>

      <div>
        {liveSteps.length > 0 ? (
          <WorkflowPanel steps={liveSteps} />
        ) : (
          <HudPanel>
            <HudTitle>Live workflow</HudTitle>
            <p className="mt-3 text-sm text-cyan-200/40">Live workflow will appear here.</p>
          </HudPanel>
        )}
      </div>
    </div>
  );
}
