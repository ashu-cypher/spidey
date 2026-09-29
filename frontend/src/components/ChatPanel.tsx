import { useState } from 'react';
import { postChat } from '../api';
import type { WorkflowRun, WorkflowStep } from '../api';
import { WorkflowPanel } from './WorkflowPanel';

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
  'what am I learning?',
];

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

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setMessages((m) => [...m, { role: 'user', text: message }]);
    setInput('');
    setBusy(true);
    setLiveSteps([]);
    try {
      const { run_id } = await postChat(message);
      const es = new EventSource(`/api/workflow/${encodeURIComponent(run_id)}/stream`);
      es.addEventListener('step_update', (e: Event) => {
        const step = JSON.parse((e as MessageEvent).data) as WorkflowStep;
        setLiveSteps((prev) => upsert(prev, step));
      });
      es.addEventListener('done', (e: Event) => {
        const run = JSON.parse((e as MessageEvent).data) as WorkflowRun;
        setLiveSteps(run.steps);
        setMessages((m) => [...m, { role: 'assistant', text: run.result ?? '(no response)' }]);
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

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
          className="flex gap-2"
        >
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
