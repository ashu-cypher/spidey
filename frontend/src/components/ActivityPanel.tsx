import { useEffect, useState } from 'react';
import { getActivity } from '../api';
import type { RunStatus, WorkflowRun } from '../api';
import { WorkflowPanel } from './WorkflowPanel';
import { HudEmpty, HudError, HudPanel } from './hud';

const STATUS_BADGE: Record<RunStatus, string> = {
  running: 'border-gold/40 bg-gold/10 text-gold',
  completed: 'border-emerald-400/40 bg-emerald-400/10 text-emerald-300',
  failed: 'border-crimson/40 bg-crimson/10 text-red-300',
  awaiting_confirmation: 'border-gold/40 bg-gold/10 text-gold',
};

interface Props {
  refreshKey: number;
}

export function ActivityPanel({ refreshKey }: Props) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getActivity()
      .then((data) => {
        if (!cancelled) {
          setRuns(data);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load activity');
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (error) return <HudError message={error} />;
  if (runs.length === 0)
    return <HudEmpty>No activity yet — send Spidey a message first.</HudEmpty>;

  return (
    <div className="relative">
      {/* HUD timeline rail */}
      <div className="absolute bottom-2 left-[7px] top-2 w-px bg-gradient-to-b from-accent/40 via-accent/10 to-transparent" aria-hidden="true" />
      <ul className="space-y-3">
        {runs.map((run) => {
          const open = openId === run.workflow_id;
          return (
            <li key={run.workflow_id} className="relative pl-6">
              <span
                className={`absolute left-[3px] top-5 h-2.5 w-2.5 rounded-full border ${
                  run.status === 'completed'
                    ? 'border-emerald-400 bg-emerald-400/40 shadow-[0_0_8px_rgba(52,211,153,0.8)]'
                    : run.status === 'failed'
                      ? 'border-crimson bg-crimson/40 shadow-[0_0_8px_rgba(239,68,68,0.8)]'
                      : 'border-gold bg-gold/40 shadow-[0_0_8px_rgba(245,158,11,0.8)] hud-blink'
                }`}
                aria-hidden="true"
              />
              <HudPanel className="!p-3">
                <button
                  type="button"
                  onClick={() => setOpenId(open ? null : run.workflow_id)}
                  className="flex w-full items-center gap-3 text-left"
                >
                  <span
                    className={`rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em] ${STATUS_BADGE[run.status]}`}
                  >
                    {run.status.replace(/_/g, ' ')}
                  </span>
                  <span className="flex-1 truncate text-sm text-cyan-100/90">{run.request}</span>
                  <span className="font-mono text-[11px] text-cyan-200/40">
                    {new Date(run.started_at).toLocaleString()}
                  </span>
                </button>
                {open && (
                  <div className="mt-3 space-y-3">
                    {run.result && (
                      <p className="whitespace-pre-wrap rounded-lg border border-accent/10 bg-carbon/60 p-3 text-sm text-cyan-100/85">
                        {run.result}
                      </p>
                    )}
                    <WorkflowPanel steps={run.steps} title="Run detail" />
                  </div>
                )}
              </HudPanel>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
