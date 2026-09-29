import { useEffect, useRef, useState } from 'react';
import {
  analyzeResume,
  compareResumeVersions,
  improveResume,
  jobMatchResume,
  listResumeVersions,
  restoreResumeVersion,
  resumeDownloadUrl,
  uploadResume,
} from '../api';
import type {
  JobMatch,
  ResumeAnalysis,
  ResumeIssue,
  ResumeSuggestion,
  ResumeVersion,
} from '../api';

type SubTab = 'analysis' | 'jobmatch' | 'versions';

const ISSUE_LABELS: Record<string, string> = {
  weak_verb: 'Weak verbs',
  vague_statement: 'Vague statements',
  repetition: 'Repetition',
  missing_measurable: 'Missing measurable outcomes',
};

const ALL_SECTIONS = [
  'summary',
  'experience',
  'education',
  'projects',
  'skills',
  'certifications',
  'achievements',
];

function ScoreBadge({ label, score }: { label: string; score: number }) {
  const color =
    score >= 80
      ? 'text-green-400 border-green-400/30 bg-green-400/10'
      : score >= 55
        ? 'text-yellow-400 border-yellow-400/30 bg-yellow-400/10'
        : 'text-red-400 border-red-400/30 bg-red-400/10';
  return (
    <div className={`rounded-xl border px-4 py-3 ${color}`}>
      <p className="font-mono text-[11px] uppercase tracking-widest opacity-80">
        {label}
      </p>
      <p className="font-mono text-2xl font-bold">{score}</p>
    </div>
  );
}

export function ResumePanel() {
  const [subTab, setSubTab] = useState<SubTab>('analysis');
  const [versions, setVersions] = useState<ResumeVersion[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [analysis, setAnalysis] = useState<ResumeAnalysis | null>(null);
  const [suggestions, setSuggestions] = useState<ResumeSuggestion[] | null>(null);
  const [suggestNote, setSuggestNote] = useState<string>('');
  const [jobMatch, setJobMatch] = useState<JobMatch | null>(null);
  const [jd, setJd] = useState('');
  const [compareA, setCompareA] = useState('');
  const [compareB, setCompareB] = useState('');
  const [diff, setDiff] = useState<string[] | null>(null);
  const [diffLabel, setDiffLabel] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = () => {
    listResumeVersions()
      .then((data) => {
        setVersions(data);
        setError(null);
        if (data.length > 0 && !selectedId) setSelectedId(data[0].id);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : 'Failed to load versions'),
      );
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || uploading) return;
    setUploading(true);
    try {
      const v = await uploadResume(file);
      setVersions((prev) => [v, ...prev]);
      setSelectedId(v.id);
      setAnalysis(null);
      setSuggestions(null);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const runGuarded = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setBusy(false);
    }
  };

  const handleAnalyze = () =>
    runGuarded(async () => {
      const a = await analyzeResume(selectedId);
      setAnalysis(a);
      setSuggestions(null);
    });

  const handleImprove = () =>
    runGuarded(async () => {
      const r = await improveResume(selectedId);
      setSuggestions(r.suggestions);
      setSuggestNote(
        `${r.note} New version v${r.new_version_number} saved.`,
      );
      refresh();
    });

  const handleJobMatch = () =>
    runGuarded(async () => {
      if (!jd.trim()) {
        setError('Paste a job description first.');
        return;
      }
      const r = await jobMatchResume(selectedId, jd.trim());
      setJobMatch(r.job_match);
      refresh();
    });

  const handleRestore = (id: string) =>
    runGuarded(async () => {
      const v = await restoreResumeVersion(id);
      setVersions((prev) => [v, ...prev]);
    });

  const handleCompare = () =>
    runGuarded(async () => {
      if (!compareA || !compareB || compareA === compareB) {
        setError('Pick two different versions to compare.');
        return;
      }
      const r = await compareResumeVersions(compareA, compareB);
      setDiff(r.diff);
      setDiffLabel(`v${r.from_version} → v${r.to_version}`);
    });

  const groupedIssues = (analysis?.issues ?? []).reduce<
    Record<string, ResumeIssue[]>
  >((acc, issue) => {
    (acc[issue.type] = acc[issue.type] || []).push(issue);
    return acc;
  }, {});

  return (
    <div className="flex flex-col gap-6">
      {/* Upload */}
      <div className="rounded-xl bg-panel border border-white/10 p-4">
        <div className="flex items-center gap-3">
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.txt"
            onChange={handleUpload}
            className="hidden"
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-black disabled:opacity-40"
          >
            {uploading ? 'Uploading…' : 'Upload CV'}
          </button>
          <span className="text-xs text-gray-500">
            .pdf, .docx, .txt — max 10 MB. Each upload starts a new version;
            versions are never overwritten.
          </span>
        </div>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {/* Sub-tabs */}
      <nav className="flex gap-1 rounded-xl bg-panel border border-white/10 p-1">
        {(
          [
            { id: 'analysis', label: 'Analysis' },
            { id: 'jobmatch', label: 'Job match' },
            { id: 'versions', label: 'Versions' },
          ] as { id: SubTab; label: string }[]
        ).map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setSubTab(t.id)}
            className={`flex-1 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              subTab === t.id
                ? 'bg-accent/15 text-accent border border-accent/30'
                : 'text-gray-400 border border-transparent hover:text-gray-200 hover:bg-white/5'
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {versions.length === 0 && (
        <p className="text-sm text-gray-500">
          No CV yet — upload one above, or say “analyze my resume” in chat after
          uploading.
        </p>
      )}

      {/* ------------------------------ Analysis ------------------------------ */}
      {subTab === 'analysis' && versions.length > 0 && (
        <div className="flex flex-col gap-4">
          <div className="flex gap-2">
            <select
              value={selectedId}
              onChange={(e) => {
                setSelectedId(e.target.value);
                setAnalysis(null);
                setSuggestions(null);
              }}
              className="flex-1 rounded-xl bg-panel border border-white/10 px-4 py-2 text-sm text-gray-200 outline-none focus:border-accent"
            >
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version_number} — {v.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleAnalyze}
              disabled={busy || !selectedId}
              className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-black disabled:opacity-40"
            >
              {busy ? 'Analyzing…' : 'Analyze'}
            </button>
          </div>

          {analysis && (
            <div className="flex flex-col gap-4">
              <div className="grid grid-cols-2 gap-3">
                <ScoreBadge label="Quality score" score={analysis.quality_score} />
                <ScoreBadge label="ATS score" score={analysis.ats.score} />
              </div>

              {/* Sections checklist */}
              <div className="rounded-xl bg-panel border border-white/10 p-4">
                <h3 className="font-mono text-xs uppercase tracking-widest text-gray-500 mb-3">
                  Sections
                </h3>
                <div className="flex flex-wrap gap-2">
                  {ALL_SECTIONS.map((s) => {
                    const found = analysis.content.sections_found.includes(s);
                    return (
                      <span
                        key={s}
                        className={`rounded-full border px-2 py-0.5 font-mono text-[11px] ${
                          found
                            ? 'bg-green-400/10 border-green-400/30 text-green-400'
                            : 'bg-white/5 border-white/10 text-gray-500'
                        }`}
                      >
                        {found ? '✓' : '✕'} {s}
                      </span>
                    );
                  })}
                </div>
                <p className="mt-3 font-mono text-[11px] text-gray-500">
                  {analysis.content.bullet_count} bullets ·{' '}
                  {analysis.content.word_count} words · keyword coverage{' '}
                  {Math.round(analysis.ats.keyword_coverage * 100)}%
                </p>
              </div>

              {/* Missing info callouts */}
              {analysis.missing_info.length > 0 && (
                <div className="rounded-xl border border-yellow-400/30 bg-yellow-400/5 p-4">
                  <h3 className="font-mono text-xs uppercase tracking-widest text-yellow-400 mb-2">
                    Missing info
                  </h3>
                  <ul className="list-disc pl-5 text-sm text-gray-300">
                    {analysis.missing_info.map((m) => (
                      <li key={m}>{m}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* ATS panel */}
              <div className="rounded-xl bg-panel border border-white/10 p-4">
                <h3 className="font-mono text-xs uppercase tracking-widest text-gray-500 mb-3">
                  ATS check
                </h3>
                <div className="flex flex-wrap gap-2 text-[11px] font-mono">
                  <span
                    className={`rounded-full border px-2 py-0.5 ${
                      analysis.ats.sections_ok
                        ? 'text-green-400 border-green-400/30'
                        : 'text-red-400 border-red-400/30'
                    }`}
                  >
                    sections {analysis.ats.sections_ok ? 'ok' : 'missing'}
                  </span>
                  <span
                    className={`rounded-full border px-2 py-0.5 ${
                      analysis.ats.contact_ok
                        ? 'text-green-400 border-green-400/30'
                        : 'text-red-400 border-red-400/30'
                    }`}
                  >
                    contact {analysis.ats.contact_ok ? 'ok' : 'missing'}
                  </span>
                </div>
                {analysis.ats.formatting_risks.length > 0 && (
                  <ul className="mt-3 list-disc pl-5 text-sm text-gray-300">
                    {analysis.ats.formatting_risks.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                )}
                {analysis.ats.formatting_risks.length === 0 && (
                  <p className="mt-3 text-sm text-gray-500">
                    No formatting risks detected.
                  </p>
                )}
              </div>

              {/* Issues grouped by type */}
              <div className="flex flex-col gap-3">
                <h3 className="font-mono text-xs uppercase tracking-widest text-gray-500">
                  Issues ({analysis.issues.length})
                </h3>
                {analysis.issues.length === 0 && (
                  <p className="text-sm text-gray-500">
                    No issues found — clean CV.
                  </p>
                )}
                {Object.entries(groupedIssues).map(([type, issues]) => (
                  <div
                    key={type}
                    className="rounded-xl bg-panel border border-white/10 p-4"
                  >
                    <h4 className="text-sm font-semibold text-accent mb-2">
                      {ISSUE_LABELS[type] ?? type} ({issues.length})
                    </h4>
                    <ul className="flex flex-col gap-2">
                      {issues.map((issue, i) => (
                        <li key={i} className="text-sm">
                          <p className="text-gray-300">{issue.detail}</p>
                          <p className="mt-1 font-mono text-[11px] text-gray-500 break-words">
                            “{issue.excerpt}”
                          </p>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>

              {/* Suggestions */}
              <div className="rounded-xl bg-panel border border-white/10 p-4">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-mono text-xs uppercase tracking-widest text-gray-500">
                    Rewrite suggestions
                  </h3>
                  <button
                    type="button"
                    onClick={handleImprove}
                    disabled={busy}
                    className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-black disabled:opacity-40"
                  >
                    {busy ? 'Working…' : 'Generate suggestions'}
                  </button>
                </div>
                <p className="text-xs text-gray-500 mb-3">
                  Suggestions only rephrase what is already in your CV —
                  Spidey never invents skills, numbers, or achievements.
                </p>
                {suggestNote && (
                  <p className="text-sm text-green-400 mb-3">{suggestNote}</p>
                )}
                {suggestions && suggestions.length === 0 && (
                  <p className="text-sm text-gray-500">
                    No safe rewrites found — the wording already looks strong.
                  </p>
                )}
                {suggestions && suggestions.length > 0 && (
                  <ul className="flex flex-col gap-3">
                    {suggestions.map((s, i) => (
                      <li
                        key={i}
                        className="rounded-lg border border-white/10 p-3"
                      >
                        <p className="text-sm text-gray-500 line-through">
                          {s.original}
                        </p>
                        <p className="mt-1 text-sm text-gray-200">{s.improved}</p>
                        <p className="mt-1 text-xs text-accent2">{s.reason}</p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ------------------------------ Job match ------------------------------ */}
      {subTab === 'jobmatch' && versions.length > 0 && (
        <div className="flex flex-col gap-4">
          <div className="flex gap-2">
            <select
              value={selectedId}
              onChange={(e) => {
                setSelectedId(e.target.value);
                setJobMatch(null);
              }}
              className="rounded-xl bg-panel border border-white/10 px-4 py-2 text-sm text-gray-200 outline-none focus:border-accent"
            >
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version_number} — {v.label}
                </option>
              ))}
            </select>
          </div>
          <textarea
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            placeholder="Paste the job description here…"
            rows={8}
            className="rounded-xl bg-panel border border-white/10 px-4 py-2 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-accent"
          />
          <div>
            <button
              type="button"
              onClick={handleJobMatch}
              disabled={busy || !jd.trim() || !selectedId}
              className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-black disabled:opacity-40"
            >
              {busy ? 'Matching…' : 'Match against this job'}
            </button>
          </div>
          <p className="text-xs text-gray-500">
            Job-match works best from this tab — chat-based job matching is not
            supported; say “analyze my resume” in chat for the analysis
            workflow instead.
          </p>

          {jobMatch && (
            <div className="flex flex-col gap-4">
              <ScoreBadge label="Match score" score={jobMatch.match_score} />
              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-xl bg-panel border border-white/10 p-4">
                  <h3 className="font-mono text-xs uppercase tracking-widest text-green-400 mb-2">
                    Matched skills ({jobMatch.matched_skills.length})
                  </h3>
                  {jobMatch.matched_skills.length === 0 ? (
                    <p className="text-sm text-gray-500">None.</p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {jobMatch.matched_skills.map((s) => (
                        <span
                          key={s}
                          className="rounded-full bg-green-400/10 border border-green-400/30 px-2 py-0.5 font-mono text-[11px] text-green-400"
                        >
                          {s}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="rounded-xl bg-panel border border-white/10 p-4">
                  <h3 className="font-mono text-xs uppercase tracking-widest text-red-400 mb-2">
                    Missing skills ({jobMatch.missing_skills.length})
                  </h3>
                  {jobMatch.missing_skills.length === 0 ? (
                    <p className="text-sm text-gray-500">None — full coverage.</p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {jobMatch.missing_skills.map((s) => (
                        <span
                          key={s}
                          className="rounded-full bg-red-400/10 border border-red-400/30 px-2 py-0.5 font-mono text-[11px] text-red-400"
                        >
                          {s}
                        </span>
                      ))}
                    </div>
                  )}
                  <p className="mt-2 text-[11px] text-gray-500">
                    Spidey never claims you have these — add them only if true.
                  </p>
                </div>
              </div>

              {jobMatch.unclear_skills.length > 0 && (
                <div className="rounded-xl border border-yellow-400/30 bg-yellow-400/5 p-4">
                  <h3 className="font-mono text-xs uppercase tracking-widest text-yellow-400 mb-2">
                    Unclear ({jobMatch.unclear_skills.length})
                  </h3>
                  <ul className="list-disc pl-5 text-sm text-gray-300">
                    {jobMatch.unclear_skills.map((u, i) => (
                      <li key={i}>{u.note}</li>
                    ))}
                  </ul>
                </div>
              )}

              {jobMatch.relevant_experience.length > 0 && (
                <div className="rounded-xl bg-panel border border-white/10 p-4">
                  <h3 className="font-mono text-xs uppercase tracking-widest text-gray-500 mb-2">
                    Relevant experience
                  </h3>
                  <ul className="list-disc pl-5 text-sm text-gray-300">
                    {jobMatch.relevant_experience.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="rounded-xl bg-panel border border-white/10 p-4">
                <h3 className="font-mono text-xs uppercase tracking-widest text-gray-500 mb-2">
                  Improvements
                </h3>
                <ul className="list-disc pl-5 text-sm text-gray-300">
                  {jobMatch.improvements.map((imp, i) => (
                    <li key={i}>{imp}</li>
                  ))}
                </ul>
              </div>

              {jobMatch.keywords_to_consider.length > 0 && (
                <div className="rounded-xl bg-panel border border-white/10 p-4">
                  <h3 className="font-mono text-xs uppercase tracking-widest text-gray-500 mb-2">
                    Keywords to consider
                  </h3>
                  <div className="flex flex-wrap gap-2">
                    {jobMatch.keywords_to_consider.map((k) => (
                      <span
                        key={k}
                        className="rounded-full bg-accent2/10 border border-accent2/30 px-2 py-0.5 font-mono text-[11px] text-accent2"
                      >
                        {k}
                      </span>
                    ))}
                  </div>
                  <p className="mt-2 text-[11px] text-gray-500">
                    Use these only where truthful — they help ATS keyword
                    matching.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ------------------------------ Versions ------------------------------ */}
      {subTab === 'versions' && versions.length > 0 && (
        <div className="flex flex-col gap-4">
          <ul className="grid gap-3">
            {versions.map((v) => (
              <li
                key={v.id}
                className="rounded-xl bg-panel border border-white/10 p-4"
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium text-gray-200">
                      v{v.version_number} — {v.label}
                    </p>
                    <p className="mt-1 font-mono text-[11px] text-gray-500">
                      {v.created_from ? `from ${v.created_from} · ` : ''}
                      {v.source_filename} ·{' '}
                      {v.created_at
                        ? new Date(v.created_at).toLocaleString()
                        : ''}
                    </p>
                  </div>
                  <span className="rounded-full bg-accent/10 border border-accent/30 px-2 py-0.5 font-mono text-[11px] text-accent">
                    v{v.version_number}
                  </span>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => handleRestore(v.id)}
                    disabled={busy}
                    className="rounded-lg px-3 py-1 text-xs border border-white/10 text-gray-300 hover:bg-white/5 disabled:opacity-40"
                  >
                    Restore as new version
                  </button>
                  <a
                    href={resumeDownloadUrl(v.id, 'txt')}
                    className="rounded-lg px-3 py-1 text-xs border border-white/10 text-gray-300 hover:bg-white/5"
                  >
                    ↓ txt
                  </a>
                  <a
                    href={resumeDownloadUrl(v.id, 'md')}
                    className="rounded-lg px-3 py-1 text-xs border border-white/10 text-gray-300 hover:bg-white/5"
                  >
                    ↓ md
                  </a>
                </div>
              </li>
            ))}
          </ul>

          <div className="rounded-xl bg-panel border border-white/10 p-4">
            <h3 className="font-mono text-xs uppercase tracking-widest text-gray-500 mb-3">
              Compare versions
            </h3>
            <div className="flex flex-wrap gap-2">
              <select
                value={compareA}
                onChange={(e) => setCompareA(e.target.value)}
                className="rounded-xl bg-panel border border-white/10 px-3 py-2 text-sm text-gray-200 outline-none"
              >
                <option value="">From…</option>
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version_number} — {v.label}
                  </option>
                ))}
              </select>
              <select
                value={compareB}
                onChange={(e) => setCompareB(e.target.value)}
                className="rounded-xl bg-panel border border-white/10 px-3 py-2 text-sm text-gray-200 outline-none"
              >
                <option value="">To…</option>
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version_number} — {v.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={handleCompare}
                disabled={busy}
                className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-black disabled:opacity-40"
              >
                Compare
              </button>
            </div>
            {diff && (
              <div className="mt-3">
                <p className="font-mono text-[11px] text-gray-500 mb-2">
                  {diffLabel}
                </p>
                <pre className="max-h-96 overflow-auto rounded-lg bg-black/40 p-3 font-mono text-[11px] leading-relaxed">
                  {diff.length === 0 ? (
                    <span className="text-gray-500">No differences.</span>
                  ) : (
                    diff.map((line, i) => (
                      <div
                        key={i}
                        className={
                          line.startsWith('+') && !line.startsWith('+++')
                            ? 'text-green-400'
                            : line.startsWith('-') && !line.startsWith('---')
                              ? 'text-red-400'
                              : line.startsWith('@@')
                                ? 'text-accent'
                                : 'text-gray-400'
                        }
                      >
                        {line}
                      </div>
                    ))
                  )}
                </pre>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
