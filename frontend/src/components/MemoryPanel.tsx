import { useEffect, useState } from 'react';
import { createMemory, deleteMemory, getMemories } from '../api';
import type { MemoryItem } from '../api';

export function MemoryPanel() {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newContent, setNewContent] = useState('');
  const [saving, setSaving] = useState(false);

  const refresh = () => {
    getMemories()
      .then((data) => {
        setMemories(data);
        setError(null);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load memories');
      });
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const content = newContent.trim();
    if (!content || saving) return;
    setSaving(true);
    try {
      const saved = await createMemory(content);
      setMemories((prev) => [saved, ...prev]);
      setNewContent('');
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save memory');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to delete memory');
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={handleAdd} className="flex gap-2">
        <input
          value={newContent}
          onChange={(e) => setNewContent(e.target.value)}
          placeholder="Tell Spidey to remember something…"
          className="flex-1 rounded-xl bg-panel border border-white/10 px-4 py-2 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-accent"
        />
        <button
          type="submit"
          disabled={saving || !newContent.trim()}
          className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-black disabled:opacity-40"
        >
          {saving ? 'Saving…' : 'Remember'}
        </button>
      </form>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {memories.length === 0 && !error ? (
        <p className="text-sm text-gray-500">No memories yet — tell Spidey to remember something.</p>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {memories.map((m) => (
            <li key={m.id} className="rounded-xl bg-panel border border-white/10 p-4">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm text-gray-200">{m.content}</p>
                <button
                  onClick={() => handleDelete(m.id)}
                  title="Delete memory"
                  className="shrink-0 rounded-lg px-2 py-1 text-xs text-gray-500 hover:bg-white/10 hover:text-red-400"
                >
                  ✕
                </button>
              </div>
              <div className="mt-3 flex items-center gap-3">
                <span className="rounded-full bg-accent2/10 border border-accent2/30 px-2 py-0.5 font-mono text-[11px] text-accent2">
                  {m.category}
                </span>
                <div className="flex-1 h-1.5 rounded-full bg-white/5">
                  <div
                    className="h-1.5 rounded-full bg-gradient-to-r from-accent to-accent2"
                    style={{ width: `${Math.min(100, Math.max(0, m.importance * 100))}%` }}
                  />
                </div>
                <span className="font-mono text-[11px] text-gray-500">
                  {new Date(m.created_at).toLocaleDateString()}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
