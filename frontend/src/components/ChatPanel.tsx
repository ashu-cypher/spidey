import { useEffect, useRef, useState } from 'react';
import { postChat } from '../api';
import type { ConfirmationPayload, WorkflowRun, WorkflowStep } from '../api';
import { WorkflowPanel } from './WorkflowPanel';
import { HudButton, HudChip, HudInput, HudPanel, HudTitle } from './hud';
import { useJarvis } from '../jarvis/context';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
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
  const {
    registerChatSend,
    setChatBusy,
    setReactorMode,
    listening,
    speak,
    ttsSupported,
    speaking,
    voice,
    logTranscript,
    bumpActivity,
  } = useJarvis();

  async function send(text: string, confirmToken?: string) {
    const message = text.trim();
    if (!message || busy) return;
    setMessages((m) => [...m, { role: 'user', text: message }]);
    setInput('');
    setBusy(true);
    setLiveSteps([]);
    try {
      const { run_id } = await postChat(message, confirmToken);
      const es = new EventSource(`/api/workflow/${encodeURIComponent(run_id)}/stream`);
      es.addEventListener('step_update', (e: Event) => {
        const step = JSON.parse((e as MessageEvent).data) as WorkflowStep;
        setLiveSteps((prev) => upsert(prev, step));
      });
      es.addEventListener('done', (e: Event) => {
        const run = JSON.parse((e as MessageEvent).data) as WorkflowRun;
        setLiveSteps(run.steps);
        const payload = parseConfirmation(run.result);
        if (payload) {
          setConfirm(payload);
          setMessages((m) => [
            ...m,
            { role: 'assistant', text: `Needs your confirmation: ${payload.proposal}` },
          ]);
        } else {
          setMessages((m) => [...m, { role: 'assistant', text: run.result ?? '(no response)' }]);
        }
        es.close();
        setBusy(false);
        bumpActivity();
      });
      es.onerror = () => {
        es.close();
        setBusy(false);
      };
    } catch {
      setMessages((m) => [
        ...m,
        { role: 'assistant', text: 'Request failed. Is the backend running at http://127.0.0.1:8000?' },
      ]);
      setBusy(false);
    }
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

  // Reactor reflects chat processing state.
  useEffect(() => {
    setChatBusy(busy);
    if (busy) setReactorMode('thinking');
    else setReactorMode(listening ? 'listening' : 'idle');
  }, [busy, setChatBusy, setReactorMode, listening]);

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
      <HudPanel title="Command console" className="flex flex-col">
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
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Message Spidey…"
            className="flex-1"
          />
          <HudButton type="submit" variant="primary" disabled={busy || !input.trim()}>
            Send
          </HudButton>
        </form>
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
