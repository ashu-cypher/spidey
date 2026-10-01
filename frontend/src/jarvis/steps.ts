// ---------------------------------------------------------------------------
// Shared workflow-step helpers: friendly labels, tool-name extraction, and
// error sanitization. Everything here is derived from REAL backend step data
// (step.type / step.name / step.input) — never invented.
// ---------------------------------------------------------------------------

import type { WorkflowStep } from '../api';

/** Tool key → friendly "doing" label shown while its step is RUNNING. */
const TOOL_LABELS: Record<string, string> = {
  tasks: 'Using Task Tool…',
  reminders: 'Checking reminders…',
  search: 'Searching…',
  rag: 'Reading document…',
  documents: 'Reading document…',
  resume: 'Analyzing résumé…',
  system: 'Running diagnostics…',
  memory: 'Saving memory…',
  calculator: 'Calculating…',
  code: 'Running code…',
  shell: 'Running command…',
};

/**
 * Extract the tool key from a "tool"-type step. The planner names tool steps
 * `Execute {tool}`; confirmation steps carry `input.tool`. Keyword matching
 * on the step name is the last resort.
 */
export function toolNameOf(step: WorkflowStep): string | null {
  const nameMatch = /^Execute\s+([A-Za-z_]+)/i.exec(step.name ?? '');
  if (nameMatch) return nameMatch[1].toLowerCase();
  const inputTool = (step.input as Record<string, unknown> | undefined)?.tool;
  if (typeof inputTool === 'string' && inputTool) return inputTool.toLowerCase();
  const hay = `${step.name ?? ''}`.toLowerCase();
  for (const key of Object.keys(TOOL_LABELS)) {
    if (hay.includes(key)) return key;
  }
  return null;
}

/** Friendly label for a workflow step, shown while it runs / in the timeline. */
export function friendlyStepLabel(step: WorkflowStep): string {
  switch (step.type) {
    case 'understand':
      return 'Understanding…';
    case 'memory':
      return 'Reading memory…';
    case 'memory_update':
      return 'Updating memory…';
    case 'tool': {
      const tool = toolNameOf(step);
      if (tool && TOOL_LABELS[tool]) return TOOL_LABELS[tool];
      return step.name ? `${step.name}…` : 'Working…';
    }
    case 'verify':
      return 'Verifying…';
    case 'respond':
      return 'Generating response…';
    case 'confirm':
      return 'Waiting for your confirmation…';
    default:
      return step.name ? `${step.name}…` : 'Working…';
  }
}

/**
 * Never render raw backend errors that may contain stack traces. The backend
 * already sanitizes most step errors; this is a defensive second layer.
 */
export function sanitizeStepError(
  err: string | null | undefined,
): string | null {
  if (!err) return null;
  if (/traceback/i.test(err) || /File ".*", line \d+/.test(err)) {
    return 'This step failed — technical details were logged on the server.';
  }
  return err;
}

/** Real signal: a completed run's steps include memory_update with saved=true. */
export function memoryWasSaved(steps: WorkflowStep[]): boolean {
  return steps.some(
    (s) =>
      s.type === 'memory_update' &&
      (s.output as Record<string, unknown> | undefined)?.saved === true,
  );
}

/** Context bucket for the contextual quick actions, from the last run's tools. */
export type RunContext = 'docs' | 'tasks' | 'none';

export function runContextOf(steps: WorkflowStep[]): RunContext {
  let tasks = false;
  let docs = false;
  for (const s of steps) {
    if (s.type !== 'tool') continue;
    const tool = toolNameOf(s);
    if (tool === 'tasks' || tool === 'reminders') tasks = true;
    if (tool === 'rag' || tool === 'documents' || tool === 'resume') docs = true;
  }
  if (docs) return 'docs';
  if (tasks) return 'tasks';
  return 'none';
}
