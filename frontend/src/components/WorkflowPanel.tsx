import type { StepStatus, WorkflowStep } from '../api';

interface StatusMeta {
  glyph: string;
  cls: string;
}

const STATUS_META: Record<StepStatus, StatusMeta> = {
  COMPLETED: { glyph: '✓', cls: 'text-green-400' },
  RUNNING: { glyph: '●', cls: 'text-amber-400 animate-pulse' },
  WAITING: { glyph: '○', cls: 'text-gray-600' },
  FAILED: { glyph: '✕', cls: 'text-red-400' },
};

function durationLabel(step: WorkflowStep): string {
  if (!step.started_at) return '…';
  const endMs = step.completed_at ? Date.parse(step.completed_at) : Date.now();
  const ms = Math.max(endMs - Date.parse(step.started_at), 0);
  return `${ms}ms`;
}

interface Props {
  steps: WorkflowStep[];
  title?: string;
}

export function WorkflowPanel({ steps, title = 'LIVE WORKFLOW' }: Props) {
  return (
    <div className="rounded-xl bg-panel border border-white/10 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-mono text-xs tracking-widest text-accent">{title}</h2>
        <span className="font-mono text-xs text-gray-500">{steps.length} steps</span>
      </div>
      {steps.length === 0 ? (
        <p className="text-sm text-gray-500">Live workflow will appear here.</p>
      ) : (
        <ul className="space-y-2">
          {steps.map((step) => {
            const meta = STATUS_META[step.status];
            const summary = JSON.stringify(step.output).slice(0, 140);
            return (
              <li key={step.step_id} className="rounded-lg bg-void/60 border border-white/5">
                <details className="group">
                  <summary className="flex cursor-pointer items-center gap-3 px-3 py-2 list-none">
                    <span className={`font-mono text-sm ${meta.cls}`}>{meta.glyph}</span>
                    <span className="flex-1 truncate text-sm text-gray-200">{step.name}</span>
                    <span className="font-mono text-[11px] text-gray-500">{durationLabel(step)}</span>
                  </summary>
                  <div className="border-t border-white/5 px-3 py-2 font-mono text-[11px] text-gray-400 space-y-1">
                    <div>
                      <span className="text-gray-600">type/tool: </span>
                      <span className="text-accent2">{step.type}</span>
                    </div>
                    <div>
                      <span className="text-gray-600">output: </span>
                      <span className="break-all">{summary || '(empty)'}</span>
                    </div>
                    {step.error && (
                      <div className="text-red-400">
                        <span className="text-gray-600">error: </span>
                        {step.error}
                      </div>
                    )}
                  </div>
                </details>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
