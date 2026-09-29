import { useEffect, useState } from 'react';
import { getActivity } from '../api';
import type { RunStatus, WorkflowRun } from '../api';
import { WorkflowPanel } from './WorkflowPanel';

const STATUS_BADGE: Record<RunStatus, string> = {
  running: 'bg-amber-400/10 text-amber-300 border-amber-400/30',
  completed: 'bg-green-400/10 text-green-300 border-green-400/30',
  failed: 'bg-red-400/10 text-red-300 border-red-400/30',
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

  if (error) return <p className="text-sm text-red-400">{error}</p>;
  if (runs.length === 0)
    return <p className="text-sm text-gray-500">No activity yet — send Spidey a message first.</p>;

  return (
    <ul className="space-y-3">
      {runs.map((run) => {
        const open = openId === run.workflow_id;
        return (
          <li key={run.workflow_id} className="rounded-xl bg-panel border border-white/10 p-4">
            <button
              type="button"
              onClick={() => setOpenId(open ? null : run.workflow_id)}
              className="flex w-full items-center gap-3 text-left"
            >
              <span
                className={`font-mono text-[11px] uppercase rounded-full border px-2 py-0.5 ${STATUS_BADGE[run.status]}`}
              >
                {run.status}
              </span>
              <span className="flex-1 truncate text-sm text-gray-200">{run.request}</span>
              <span className="font-mono text-[11px] text-gray-500">
                {new Date(run.started_at).toLocaleString()}
              </span>
            </button>
            {open && (
              <div className="mt-3 space-y-3">
                {run.result && (
                  <p className="whitespace-pre-wrap rounded-lg bg-white/5 border border-white/10 p-3 text-sm text-gray-200">
                    {run.result}
                  </p>
                )}
                <WorkflowPanel steps={run.steps} title="RUN DETAIL" />
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
