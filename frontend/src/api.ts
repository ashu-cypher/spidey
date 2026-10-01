// Typed client for the SPIDEY FastAPI backend (Phase 1 contract).

export type StepStatus = 'WAITING' | 'RUNNING' | 'COMPLETED' | 'FAILED';
export type RunStatus = 'running' | 'completed' | 'failed' | 'awaiting_confirmation';

export interface ConfirmationPayload {
  needs_confirmation: boolean;
  proposal: string;
  confirm_token: string;
}

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

export interface ChatHistoryTurn {
  role: 'user' | 'assistant';
  content: string;
}

export async function postChat(
  message: string,
  confirmToken?: string,
  history?: ChatHistoryTurn[],
): Promise<{ run_id: string }> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      confirm_token: confirmToken ?? null,
      history: history ?? [],
    }),
  });
  return json(res);
}

// --- Proactive briefing (optional; backend may not implement it) ------------

export interface Briefing {
  pending_tasks: number;
  due_soon: { text: string; remind_at: string | null }[];
}

/**
 * Fetch the proactive briefing. Returns null on ANY failure (404, network,
 * malformed) so callers can skip silently.
 */
export async function getBriefing(): Promise<Briefing | null> {
  try {
    const res = await fetch('/api/briefing');
    if (!res.ok) return null;
    const data = (await res.json()) as Partial<Briefing>;
    return {
      pending_tasks:
        typeof data.pending_tasks === 'number' ? data.pending_tasks : 0,
      due_soon: Array.isArray(data.due_soon) ? data.due_soon : [],
    };
  } catch {
    return null;
  }
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

export async function createTask(title: string, due?: string | null): Promise<TaskItem> {
  const res = await fetch('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, due: due ?? null }),
  });
  const data = await json<{ task: TaskItem }>(res);
  return data.task;
}

export async function setTaskDone(id: string, done: boolean): Promise<TaskItem> {
  const res = await fetch(`/api/tasks/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ done }),
  });
  const data = await json<{ task: TaskItem }>(res);
  return data.task;
}

export async function deleteTask(id: string): Promise<void> {
  const res = await fetch(`/api/tasks/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  await json(res);
}

// --- Reminders (Phase 5) ----------------------------------------------------

export interface ReminderItem {
  id: string;
  title: string;
  text: string;
  remind_at: string | null;
  done: boolean;
  created_at: string | null;
}

export async function getReminders(): Promise<ReminderItem[]> {
  const res = await fetch('/api/reminders');
  const data = await json<{ reminders: ReminderItem[] }>(res);
  return data.reminders;
}

export async function createReminder(
  title: string,
  remindAt?: string | null,
): Promise<ReminderItem> {
  const res = await fetch('/api/reminders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, remind_at: remindAt ?? null }),
  });
  const data = await json<{ reminder: ReminderItem }>(res);
  return data.reminder;
}

export async function setReminderDone(id: string, done: boolean): Promise<ReminderItem> {
  const res = await fetch(`/api/reminders/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ done }),
  });
  const data = await json<{ reminder: ReminderItem }>(res);
  return data.reminder;
}

export async function deleteReminder(id: string): Promise<void> {
  const res = await fetch(`/api/reminders/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  await json(res);
}

// --- Generated documents (Phase 5) ------------------------------------------

export interface GeneratedDoc {
  id: string;
  title: string;
  format: string;
  created_at: string | null;
  download_url: string;
}

export async function listDocs(): Promise<GeneratedDoc[]> {
  const res = await fetch('/api/docs');
  const data = await json<{ documents: GeneratedDoc[] }>(res);
  return data.documents;
}

export async function createDoc(
  title: string,
  content: string,
  format: 'md' | 'txt',
): Promise<GeneratedDoc> {
  const res = await fetch('/api/docs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, content, format }),
  });
  const data = await json<{ document: GeneratedDoc }>(res);
  return data.document;
}

export async function deleteDoc(id: string): Promise<void> {
  const res = await fetch(`/api/docs/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  await json(res);
}

export function docDownloadUrl(id: string): string {
  return `/api/docs/${encodeURIComponent(id)}/download`;
}

export async function getHealth(): Promise<Health> {
  const res = await fetch('/api/health');
  return json(res);
}

// --- Knowledge base (Phase 3) ----------------------------------------------

export interface KnowledgeDocument {
  id: string;
  filename: string;
  title: string;
  content_type: string;
  status: string;
  chunk_count: number;
  created_at: string | null;
}

export interface KnowledgeResult {
  chunk_id: string;
  chunk_index: number;
  content: string;
  document_id: string;
  source: string;
  created_at: string | null;
  score: number;
}

export async function listDocuments(): Promise<KnowledgeDocument[]> {
  const res = await fetch('/api/knowledge/documents');
  const data = await json<{ documents: KnowledgeDocument[] }>(res);
  return data.documents;
}

export async function uploadDocument(file: File): Promise<KnowledgeDocument> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/api/knowledge/upload', { method: 'POST', body: form });
  const data = await json<{ document: KnowledgeDocument }>(res);
  return data.document;
}

export async function deleteDocument(id: string): Promise<void> {
  const res = await fetch(
    `/api/knowledge/documents/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  );
  await json(res);
}

export async function searchDocuments(
  q: string,
  limit = 5,
): Promise<KnowledgeResult[]> {
  const res = await fetch(
    `/api/knowledge/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  );
  const data = await json<{ results: KnowledgeResult[] }>(res);
  return data.results;
}


// --- Resume (Phase 4) -------------------------------------------------------

export interface ResumeVersion {
  id: string;
  version_number: number;
  label: string;
  source_filename: string;
  created_from: string | null;
  created_at: string | null;
  content: string;
}

export interface ResumeIssue {
  type: string;
  detail: string;
  excerpt: string;
}

export interface ResumeAnalysis {
  version_id: string | null;
  version_number: number | null;
  content: {
    sections_found: string[];
    sections_missing: string[];
    bullet_count: number;
    word_count: number;
  };
  issues: ResumeIssue[];
  issue_counts: Record<string, number>;
  quality_score: number;
  ats: {
    sections_ok: boolean;
    contact_ok: boolean;
    keyword_coverage: number;
    formatting_risks: string[];
    score: number;
  };
  missing_info: string[];
  contact: {
    name: string;
    email: string | null;
    phone: string | null;
    linkedin: string | null;
    github: string | null;
    location: string | null;
  };
}

export interface ResumeSuggestion {
  original: string;
  improved: string;
  reason: string;
}

export interface UnclearSkill {
  jd_skill: string;
  cv_skill: string;
  note: string;
}

export interface JobMatch {
  matched_skills: string[];
  missing_skills: string[];
  unclear_skills: UnclearSkill[];
  relevant_experience: string[];
  improvements: string[];
  keywords_to_consider: string[];
  match_score: number;
  jd_skill_count: number;
}

export async function uploadResume(file: File): Promise<ResumeVersion> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/api/resume/upload', { method: 'POST', body: form });
  const data = await json<{ version: ResumeVersion }>(res);
  return data.version;
}

export async function analyzeResume(versionId: string): Promise<ResumeAnalysis> {
  const res = await fetch('/api/resume/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version_id: versionId }),
  });
  const data = await json<{ analysis: ResumeAnalysis }>(res);
  return data.analysis;
}

export async function improveResume(
  versionId: string,
): Promise<{
  suggestions: ResumeSuggestion[];
  new_version_id: string;
  new_version_number: number;
  note: string;
}> {
  const res = await fetch(
    `/api/resume/versions/${encodeURIComponent(versionId)}/improve`,
    { method: 'POST' },
  );
  return json(res);
}

export async function jobMatchResume(
  versionId: string,
  jobDescription: string,
): Promise<{ job_match: JobMatch; version: ResumeVersion }> {
  const res = await fetch('/api/resume/job-match', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version_id: versionId, job_description: jobDescription }),
  });
  return json(res);
}

export async function listResumeVersions(): Promise<ResumeVersion[]> {
  const res = await fetch('/api/resume/versions');
  const data = await json<{ versions: ResumeVersion[] }>(res);
  return data.versions;
}

export async function restoreResumeVersion(id: string): Promise<ResumeVersion> {
  const res = await fetch(
    `/api/resume/versions/${encodeURIComponent(id)}/restore`,
    { method: 'POST' },
  );
  const data = await json<{ version: ResumeVersion }>(res);
  return data.version;
}

export async function compareResumeVersions(
  id: string,
  otherId: string,
): Promise<{ from_version: number; to_version: number; diff: string[] }> {
  const res = await fetch(
    `/api/resume/versions/${encodeURIComponent(id)}/compare/${encodeURIComponent(otherId)}`,
  );
  return json(res);
}

export function resumeDownloadUrl(id: string, format: 'txt' | 'md'): string {
  return `/api/resume/versions/${encodeURIComponent(id)}/download?format=${format}`;
}

// --- System telemetry (J.A.R.V.I.S. mission) ---------------------------------

export interface SystemMetrics {
  cpu_percent: number;
  ram: { percent: number; used_gb: number; total_gb: number };
  disk: { percent: number };
  uptime_seconds: number;
  battery: { percent: number; plugged: boolean } | null;
  host: string;
}

export async function getSystemMetrics(): Promise<SystemMetrics> {
  const res = await fetch('/api/system/metrics');
  return json<SystemMetrics>(res);
}

// --- Security protocols (J.A.R.V.I.S. mission) --------------------------------

export interface ProtocolDef {
  id: string;
  name: string;
  description: string;
  sfx: string;
}

export interface ProtocolTriggerResult {
  id: string;
  name: string;
  response_text: string;
  sfx: string;
  audit_id: string;
}

export interface ProtocolAuditEntry {
  id: string;
  protocol_id: string;
  protocol_name: string;
  response_text: string;
  triggered_at: string;
}

export async function getProtocols(): Promise<ProtocolDef[]> {
  const res = await fetch('/api/protocols');
  return json<ProtocolDef[]>(res);
}

export async function triggerProtocol(id: string): Promise<ProtocolTriggerResult> {
  const res = await fetch('/api/protocols/trigger', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  });
  return json<ProtocolTriggerResult>(res);
}

export async function getProtocolAudit(): Promise<ProtocolAuditEntry[]> {
  const res = await fetch('/api/protocols/audit');
  return json<ProtocolAuditEntry[]>(res);
}
