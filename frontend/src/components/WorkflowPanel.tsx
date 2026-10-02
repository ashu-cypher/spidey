import type { StepStatus, WorkflowStep } from '../api';
import { HudPanel } from './hud';
import {
  friendlyStepLabel,
  sanitizeStepError,
  toolNameOf,
} from '../mew/steps';

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

/**
 * Live workflow timeline — renders REAL steps from the SSE stream with
 * checkmarks as they complete. Entirely stream-driven; no timers.
 */
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
            const tool = step.type === 'tool' ? toolNameOf(step) : null;
            const safeError = sanitizeStepError(step.error);
            return (
              <li key={step.step_id} className="rounded-lg border border-accent/10 bg-carbon/60">
                <details className="group">
                  <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-2">
                    <span className={`font-mono text-sm ${meta.cls}`}>{meta.glyph}</span>
                    <span className="flex-1 truncate text-sm text-cyan-100/90">
                      {friendlyStepLabel(step)}
                    </span>
                    {tool && (
                      <span className="rounded-full border border-violet-400/30 bg-violet-400/10 px-2 py-0.5 font-mono text-[10px] text-violet-300">
                        {tool}
                      </span>
                    )}
                    <span className="font-mono text-[11px] text-cyan-200/40">{durationLabel(step)}</span>
                  </summary>
                  <div className="space-y-1 border-t border-accent/10 px-3 py-2 font-mono text-[11px] text-cyan-200/60">
                    <div>
                      <span className="text-cyan-200/30">step: </span>
                      <span className="text-cyan-100/80">{step.name}</span>
                    </div>
                    <div>
                      <span className="text-cyan-200/30">type: </span>
                      <span className="text-violet-300">{step.type}</span>
                    </div>
                    <div>
                      <span className="text-cyan-200/30">output: </span>
                      <span className="break-all">{summary || '(empty)'}</span>
                    </div>
                    {safeError && (
                      <div className="text-red-300">
                        <span className="text-cyan-200/30">error: </span>
                        {safeError}
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
