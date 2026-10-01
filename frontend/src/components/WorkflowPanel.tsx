import type { StepStatus, WorkflowStep } from '../api';
import { HudPanel } from './hud';

interface StatusMeta {
  glyph: string;
  cls: string;
}

const STATUS_META: Record<StepStatus, StatusMeta> = {
  COMPLETED: { glyph: '✓', cls: 'text-emerald-400' },
  RUNNING: { glyph: '●', cls: 'text-gold hud-blink' },
  WAITING: { glyph: '○', cls: 'text-slate-600' },
  FAILED: { glyph: '✕', cls: 'text-crimson' },
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

export function WorkflowPanel({ steps, title = 'Live workflow' }: Props) {
  return (
    <HudPanel
      title={title}
      right={<span className="font-mono text-xs text-cyan-200/40">{steps.length} steps</span>}
    >
      {steps.length === 0 ? (
        <p className="text-sm text-cyan-200/40">Live workflow will appear here.</p>
      ) : (
        <ul className="space-y-2">
          {steps.map((step) => {
            const meta = STATUS_META[step.status];
            const summary = JSON.stringify(step.output).slice(0, 140);
            return (
              <li key={step.step_id} className="rounded-lg border border-accent/10 bg-carbon/60">
                <details className="group">
                  <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-2">
                    <span className={`font-mono text-sm ${meta.cls}`}>{meta.glyph}</span>
                    <span className="flex-1 truncate text-sm text-cyan-100/90">{step.name}</span>
                    <span className="font-mono text-[11px] text-cyan-200/40">{durationLabel(step)}</span>
                  </summary>
                  <div className="space-y-1 border-t border-accent/10 px-3 py-2 font-mono text-[11px] text-cyan-200/60">
                    <div>
                      <span className="text-cyan-200/30">type/tool: </span>
                      <span className="text-violet-300">{step.type}</span>
                    </div>
                    <div>
                      <span className="text-cyan-200/30">output: </span>
                      <span className="break-all">{summary || '(empty)'}</span>
                    </div>
                    {step.error && (
                      <div className="text-red-300">
                        <span className="text-cyan-200/30">error: </span>
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
    </HudPanel>
  );
}
