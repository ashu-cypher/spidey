import { useEffect, useState } from 'react';
import { postChat } from '../api';
import type { ConfirmationPayload, WorkflowRun, WorkflowStep } from '../api';
import { WorkflowPanel } from './WorkflowPanel';
import { useSpeechRecognition, useTextToSpeech } from '../hooks/useVoice';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

interface Props {
  onActivity?: () => void;
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

export function ChatPanel({ onActivity }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [liveSteps, setLiveSteps] = useState<WorkflowStep[]>([]);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmationPayload | null>(null);
  const stt = useSpeechRecognition();
  const tts = useTextToSpeech();

  // When voice input finishes, send the final transcript as the chat message.
  useEffect(() => {
    if (stt.finalTranscript && !stt.listening && !busy) {
      const text = stt.finalTranscript;
      stt.clear();
      void send(text);
    }
    // send is stable enough here; re-running on it would double-send.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stt.finalTranscript, stt.listening, busy]);

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
        if (onActivity) onActivity();
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
    <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
      <div className="rounded-xl bg-panel border border-white/10 p-4 flex flex-col">
        <div className="mb-3 h-[380px] overflow-y-auto space-y-3 pr-1">
          {messages.length === 0 && (
            <p className="text-sm text-gray-500">
              Ask Spidey anything. Watch the live workflow light up on the right.
            </p>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[80%] rounded-xl px-4 py-2 text-sm whitespace-pre-wrap ${
                  m.role === 'user'
                    ? 'bg-accent/15 border border-accent/30 text-cyan-100'
                    : 'bg-white/5 border border-white/10 text-gray-200'
                }`}
              >
                {m.text}
                {m.role === 'assistant' && (
                  <div className="mt-1.5 flex gap-1">
                    <button
                      type="button"
                      title={tts.supported ? 'Read this reply aloud' : 'Voice not supported in this browser'}
                      disabled={!tts.supported}
                      onClick={() => tts.speak(m.text)}
                      className="rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 text-xs text-gray-400 hover:border-accent/50 hover:text-accent disabled:opacity-40"
                    >
                      🔊
                    </button>
                    {tts.speaking && (
                      <button
                        type="button"
                        title="Stop reading"
                        onClick={() => tts.stop()}
                        className="rounded-md border border-red-400/40 bg-red-400/10 px-1.5 py-0.5 text-xs text-red-300 hover:bg-red-400/20"
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
              <div className="rounded-xl bg-white/5 border border-white/10 px-4 py-2 text-sm text-gray-400 animate-pulse">
                Spidey is thinking…
              </div>
            </div>
          )}
        </div>

        <div className="mb-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => send(s)}
              disabled={busy}
              className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-gray-300 hover:border-accent/50 hover:text-accent disabled:opacity-40"
            >
              {s}
            </button>
          ))}
        </div>

        {confirm && (
          <div className="mb-3 rounded-xl border border-amber-400/40 bg-amber-400/10 p-4">
            <p className="mb-1 text-sm font-medium text-amber-200">
              Confirmation needed
            </p>
            <p className="mb-3 text-sm text-gray-200">{confirm.proposal}</p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void approveConfirm()}
                disabled={busy}
                className="rounded-lg bg-amber-400/20 border border-amber-400/50 px-4 py-1.5 text-sm font-medium text-amber-100 hover:bg-amber-400/30 disabled:opacity-40"
              >
                Confirm
              </button>
              <button
                type="button"
                onClick={cancelConfirm}
                className="rounded-lg border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-gray-300 hover:bg-white/10"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {stt.listening && (
          <p className="mb-2 flex items-center gap-2 text-sm italic text-gray-400" aria-live="polite">
            <span className="inline-block h-2 w-2 rounded-full bg-red-500 animate-ping" />
            Listening… {stt.transcript}
          </p>
        )}
        {stt.error && (
          <p className="mb-2 text-xs text-red-400">{stt.error}</p>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
          className="flex gap-2"
        >          {stt.supported ? (
            <button
              type="button"
              onClick={() => (stt.listening ? stt.stop() : stt.start())}
              disabled={busy}
              title={stt.listening ? 'Stop listening' : 'Speak your message (voice input)'}
              className={`rounded-lg border px-4 py-2 text-sm transition-colors disabled:opacity-40 ${
                stt.listening
                  ? 'border-red-400/60 bg-red-400/15 text-red-200'
                  : 'border-white/10 bg-white/5 text-gray-300 hover:border-accent/50 hover:text-accent'
              }`}
            >
              <span className={stt.listening ? 'mr-1 inline-block h-2 w-2 rounded-full bg-red-500 animate-pulse' : ''} />
              🎤
            </button>
          ) : (
            <button
              type="button"
              disabled
              title="Voice not supported in this browser"
              className="rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-sm text-gray-600 opacity-40 cursor-not-allowed"
            >
              🎤
            </button>
          )}
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Message Spidey…"
            className="flex-1 rounded-lg bg-void border border-white/10 px-4 py-2 text-sm text-gray-100 placeholder-gray-600 outline-none focus:border-accent/60"
          />
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="rounded-lg bg-accent/20 border border-accent/40 px-5 py-2 text-sm font-medium text-accent hover:bg-accent/30 disabled:opacity-40"
          >
            Send
          </button>
        </form>
      </div>

      <div>
        {liveSteps.length > 0 ? (
          <WorkflowPanel steps={liveSteps} />
        ) : (
          <div className="rounded-xl bg-panel border border-white/10 p-4">
            <h2 className="mb-3 font-mono text-xs tracking-widest text-accent">LIVE WORKFLOW</h2>
            <p className="text-sm text-gray-500">Live workflow will appear here.</p>
          </div>
        )}
      </div>
    </div>
  );
}
