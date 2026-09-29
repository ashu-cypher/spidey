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
