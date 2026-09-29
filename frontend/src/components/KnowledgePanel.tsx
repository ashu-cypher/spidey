import { useEffect, useRef, useState } from 'react';
import {
  deleteDocument,
  listDocuments,
  searchDocuments,
  uploadDocument,
} from '../api';
import type { KnowledgeDocument, KnowledgeResult } from '../api';

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
    <div className="flex flex-col gap-6">
      {/* Upload */}
      <div className="rounded-xl bg-panel border border-white/10 p-4">
        <div className="flex items-center gap-3">
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.txt,.md"
            onChange={handleUpload}
            className="hidden"
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-black disabled:opacity-40"
          >
            {uploading ? 'Uploading…' : 'Upload document'}
          </button>
          <span className="text-xs text-gray-500">
            .pdf, .docx, .txt, .md — max 10 MB
          </span>
        </div>
      </div>

      {/* Search */}
      <form onSubmit={handleSearch} className="flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search your documents…"
          className="flex-1 rounded-xl bg-panel border border-white/10 px-4 py-2 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-accent"
        />
        <button
          type="submit"
          disabled={searching || !query.trim()}
          className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-black disabled:opacity-40"
        >
          {searching ? 'Searching…' : 'Search'}
        </button>
      </form>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {/* Search results with citations */}
      {results !== null && (
        <div className="flex flex-col gap-2">
          <h2 className="font-mono text-xs uppercase tracking-widest text-gray-500">
            Results ({results.length})
          </h2>
          {results.length === 0 ? (
            <p className="text-sm text-gray-500">
              No matching passages found in your documents.
            </p>
          ) : (
            <ul className="grid gap-3">
              {results.map((r) => (
                <li
                  key={r.chunk_id}
                  className="rounded-xl bg-panel border border-white/10 p-4"
                >
                  <p className="text-sm text-gray-200">{r.content}</p>
                  <div className="mt-3 flex flex-wrap items-center gap-3">
                    <span className="rounded-full bg-accent/10 border border-accent/30 px-2 py-0.5 font-mono text-[11px] text-accent">
                      [{r.source}, chunk {r.chunk_index}]
                    </span>
                    <span className="font-mono text-[11px] text-gray-500">
                      score {r.score.toFixed(3)}
                    </span>
                    <span className="font-mono text-[11px] text-gray-500">
                      {r.created_at
                        ? new Date(r.created_at).toLocaleDateString()
                        : ''}
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
        <h2 className="font-mono text-xs uppercase tracking-widest text-gray-500">
          Documents ({docs.length})
        </h2>
        {docs.length === 0 ? (
          <p className="text-sm text-gray-500">
            No documents yet — upload one to build your knowledge base.
          </p>
        ) : (
          <ul className="grid gap-3 md:grid-cols-2">
            {docs.map((d) => (
              <li
                key={d.id}
                className="rounded-xl bg-panel border border-white/10 p-4"
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm font-medium text-gray-200 break-all">
                    {d.filename || d.title}
                  </p>
                  <button
                    onClick={() => handleDelete(d.id)}
                    title="Delete document"
                    className="shrink-0 rounded-lg px-2 py-1 text-xs text-gray-500 hover:bg-white/10 hover:text-red-400"
                  >
                    ✕
                  </button>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <span className="rounded-full bg-accent2/10 border border-accent2/30 px-2 py-0.5 font-mono text-[11px] text-accent2">
                    {d.content_type.split('/')[1] || d.content_type}
                  </span>
                  <span className="rounded-full bg-white/5 border border-white/10 px-2 py-0.5 font-mono text-[11px] text-gray-400">
                    {d.status}
                  </span>
                  <span className="font-mono text-[11px] text-gray-500">
                    {d.chunk_count} chunk{d.chunk_count === 1 ? '' : 's'}
                  </span>
                  <span className="font-mono text-[11px] text-gray-500">
                    {d.created_at
                      ? new Date(d.created_at).toLocaleDateString()
                      : ''}
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
