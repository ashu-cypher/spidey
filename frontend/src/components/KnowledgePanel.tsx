import { useEffect, useRef, useState } from 'react';
import {
  deleteDocument,
  listDocuments,
  searchDocuments,
  uploadDocument,
} from '../api';
import type { KnowledgeDocument, KnowledgeResult } from '../api';
import { HudButton, HudEmpty, HudError, HudInput, HudPanel, HudTitle } from './hud';

export function KnowledgePanel() {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<KnowledgeResult[] | null>(null);
  const [searching, setSearching] = useState(false);
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

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q || searching) return;
    setSearching(true);
    try {
      const res = await searchDocuments(q);
      setResults(res);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Search failed');
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Upload */}
      <HudPanel title="Document intake">
        <div className="flex items-center gap-3">
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
            {uploading ? 'Uploading…' : 'Upload document'}
          </HudButton>
          <span className="text-xs text-cyan-200/40">
            .pdf, .docx, .txt, .md — max 10 MB
          </span>
        </div>
      </HudPanel>

      {/* Search */}
      <HudPanel title="Archive search">
        <form onSubmit={handleSearch} className="flex gap-2">
          <HudInput
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search your documents…"
            className="flex-1"
          />
          <HudButton type="submit" variant="primary" disabled={searching || !query.trim()}>
            {searching ? 'Scanning…' : 'Search'}
          </HudButton>
        </form>
      </HudPanel>

      {error && <HudError message={error} />}

      {/* Search results with citations */}
      {results !== null && (
        <div className="flex flex-col gap-2">
          <HudTitle>Results ({results.length})</HudTitle>
          {results.length === 0 ? (
            <HudEmpty>No matching passages found in your documents.</HudEmpty>
          ) : (
            <ul className="grid gap-3">
              {results.map((r) => (
                <li key={r.chunk_id} className="hud-panel p-4">
                  <p className="text-sm text-cyan-100/90">{r.content}</p>
                  <div className="mt-3 flex flex-wrap items-center gap-3">
                    <span className="rounded-full border border-accent/30 bg-accent/10 px-2 py-0.5 font-mono text-[11px] text-accent">
                      [{r.source}, chunk {r.chunk_index}]
                    </span>
                    <span className="font-mono text-[11px] text-cyan-200/40">
                      score {r.score.toFixed(3)}
                    </span>
                    <span className="font-mono text-[11px] text-cyan-200/40">
                      {r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Document list */}
      <div className="flex flex-col gap-2">
        <HudTitle>Documents ({docs.length})</HudTitle>
        {docs.length === 0 ? (
          <HudEmpty>No documents yet — upload one to build your knowledge base.</HudEmpty>
        ) : (
          <ul className="grid gap-3 md:grid-cols-2">
            {docs.map((d) => (
              <li key={d.id} className="hud-panel p-4">
                <div className="flex items-start justify-between gap-2">
                  <p className="break-all text-sm font-medium text-cyan-100/90">
                    {d.filename || d.title}
                  </p>
                  <button
                    onClick={() => handleDelete(d.id)}
                    title="Delete document"
                    className="shrink-0 rounded-lg px-2 py-1 text-xs text-cyan-200/40 hover:bg-crimson/10 hover:text-red-300"
                  >
                    ✕
                  </button>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <span className="rounded-full border border-violet-400/30 bg-violet-400/10 px-2 py-0.5 font-mono text-[11px] text-violet-300">
                    {d.content_type.split('/')[1] || d.content_type}
                  </span>
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
      </div>
    </div>
  );
}
