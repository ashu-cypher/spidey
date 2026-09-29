// Typed client for the SPIDEY FastAPI backend (Phase 1 contract).

export type StepStatus = 'WAITING' | 'RUNNING' | 'COMPLETED' | 'FAILED';
export type RunStatus = 'running' | 'completed' | 'failed';

export interface WorkflowStep {
  step_id: string;
  workflow_run_id: string;
  name: string;
  type: string;
  status: StepStatus;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

export interface WorkflowRun {
  workflow_id: string;
  user_id: string;
  request: string;
  status: RunStatus;
  started_at: string;
  completed_at: string | null;
  result: string | null;
  steps: WorkflowStep[];
}

export interface MemoryItem {
  id: string;
  content: string;
  category: string;
  importance: number;
  created_at: string;
}

export interface TaskItem {
  id: string;
  title: string;
  due: string | null;
  done: boolean;
  created_at: string;
}

export interface Health {
  status: string;
  provider: string;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export async function postChat(message: string): Promise<{ run_id: string }> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
  return json(res);
}

export async function getWorkflow(run_id: string): Promise<WorkflowRun> {
  const res = await fetch(`/api/workflow/${encodeURIComponent(run_id)}`);
  return json(res);
}

export async function getActivity(): Promise<WorkflowRun[]> {
  const res = await fetch('/api/activity');
  return json(res);
}

export async function getMemories(): Promise<MemoryItem[]> {
  const res = await fetch('/api/memory');
  const data = await json<{ memories: MemoryItem[] }>(res);
  return data.memories;
}

export async function createMemory(
  content: string,
  category?: string,
  importance?: number,
): Promise<MemoryItem> {
  const res = await fetch('/api/memory', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, category, importance }),
  });
  const data = await json<{ saved: MemoryItem }>(res);
  return data.saved;
}

export async function deleteMemory(id: string): Promise<void> {
  const res = await fetch(`/api/memory/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  await json(res);
}

export async function getTasks(): Promise<TaskItem[]> {
  const res = await fetch('/api/tasks');
  const data = await json<{ tasks: TaskItem[] }>(res);
  return data.tasks;
}

export async function getHealth(): Promise<Health> {
  const res = await fetch('/api/health');
  return json(res);
}
