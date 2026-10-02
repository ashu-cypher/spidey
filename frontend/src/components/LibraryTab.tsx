import { useEffect, useRef, useState } from 'react';
import {
  deleteDocument,
  listDocuments,
  listResumeVersions,
  resumeDownloadUrl,
  restoreResumeVersion,
  uploadDocument,
} from '../api';
import type { KnowledgeDocument, ResumeVersion } from '../api';
import { HudButton, HudEmpty, HudError, HudPanel, HudTitle } from './hud';

// ---------------------------------------------------------------------------
// Library — management-only lists. No analysis UI here: documents are
// attached and discussed in the MEW conversation; resume versions are
// downloaded or restored. Upload stays as plain list management.
// ---------------------------------------------------------------------------

function DocumentsSection() {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = () => {
    listDocuments()
      .then((data) => {
        setDocs(data);
        setError(null);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load documents');
      });
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || uploading) return;
    setUploading(true);
    try {
      const doc = await uploadDocument(file);
      setDocs((prev) => [doc, ...prev]);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteDocument(id);
      setDocs((prev) => prev.filter((d) => d.id !== id));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to delete document');
    }
  };

  return (
    <HudPanel
      title="Documents"
      right={
        <span className="font-mono text-xs text-cyan-200/40">
          {docs.length} stored
        </span>
      }
    >
      <div className="mb-4 flex items-center gap-3">
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt,.md"
          onChange={handleUpload}
          className="hidden"
        />
        <HudButton
          variant="primary"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
        >
          {uploading ? 'Uploading…' : '+ Add document'}
        </HudButton>
        <span className="text-xs text-cyan-200/40">.pdf, .docx, .txt, .md</span>
      </div>

      {error && <HudError message={error} />}

      {docs.length === 0 ? (
        <HudEmpty>
          No documents stored. Attach files in the MEW conversation, or add one
          here for the knowledge base.
        </HudEmpty>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {docs.map((d) => (
            <li key={d.id} className="hud-panel p-4">
              <div className="flex items-start justify-between gap-2">
                <p className="break-all text-sm font-medium text-cyan-100/90">
                  {d.filename || d.title}
                </p>
                <button
                  onClick={() => void handleDelete(d.id)}
                  title="Delete document"
                  className="shrink-0 rounded-lg px-2 py-1 text-xs text-cyan-200/40 hover:bg-crimson/10 hover:text-red-300"
                >
                  ✕
                </button>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <span className="rounded-full border border-accent/20 bg-accent/5 px-2 py-0.5 font-mono text-[11px] text-cyan-200/60">
                  {d.status}
                </span>
                <span className="font-mono text-[11px] text-cyan-200/40">
                  {d.chunk_count} chunk{d.chunk_count === 1 ? '' : 's'}
                </span>
                <span className="font-mono text-[11px] text-cyan-200/40">
                  {d.created_at ? new Date(d.created_at).toLocaleDateString() : ''}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </HudPanel>
  );
}

function ResumesSection() {
  const [versions, setVersions] = useState<ResumeVersion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const refresh = () => {
    listResumeVersions()
      .then((data) => {
        setVersions(data);
        setError(null);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load resume versions');
      });
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleRestore = async (id: string) => {
    setWorking(true);
    try {
      await restoreResumeVersion(id);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to restore version');
    } finally {
      setWorking(false);
    }
  };

  return (
    <HudPanel
      title="Resume versions"
      right={
        <span className="font-mono text-xs text-cyan-200/40">
          {versions.length} stored
        </span>
      }
    >
      {error && <HudError message={error} />}

      {versions.length === 0 ? (
        <HudEmpty>
          No resume versions yet. Ask MEW in the conversation to work with your
          resume.
        </HudEmpty>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {versions.map((v) => (
            <li key={v.id} className="hud-panel p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-medium text-cyan-100/90">
                    v{v.version_number}
                    {v.label ? ` — ${v.label}` : ''}
                  </p>
                  <p className="mt-0.5 break-all font-mono text-[11px] text-cyan-200/40">
                    {v.source_filename}
                  </p>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <a
                  href={resumeDownloadUrl(v.id, 'md')}
                  className="hud-chip"
                  title="Download as Markdown"
                >
                  ⭳ .md
                </a>
                <a
                  href={resumeDownloadUrl(v.id, 'txt')}
                  className="hud-chip"
                  title="Download as text"
                >
                  ⭳ .txt
                </a>
                <HudButton
                  onClick={() => void handleRestore(v.id)}
                  disabled={working}
                  title="Restore this version as current"
                >
                  Restore
                </HudButton>
                <span className="font-mono text-[11px] text-cyan-200/40">
                  {v.created_at ? new Date(v.created_at).toLocaleDateString() : ''}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-cyan-200/40">
        Analysis, improvements, and job matching happen in the MEW
        conversation — just ask.
      </p>
    </HudPanel>
  );
}

export function LibraryTab() {
  return (
    <div className="flex flex-col gap-6">
      <HudTitle>Library</HudTitle>
      <DocumentsSection />
      <ResumesSection />
    </div>
  );
}
