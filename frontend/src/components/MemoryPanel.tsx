import { useEffect, useState } from 'react';
import { createMemory, deleteMemory, getMemories } from '../api';
import type { MemoryItem } from '../api';
import { HudButton, HudEmpty, HudError, HudInput, HudPanel } from './hud';

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
      <HudPanel title="Memory core">
        <form onSubmit={handleAdd} className="flex gap-2">
          <HudInput
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
            placeholder="Tell Spidey to remember something…"
            className="flex-1"
          />
          <HudButton type="submit" variant="primary" disabled={saving || !newContent.trim()}>
            {saving ? 'Storing…' : 'Remember'}
          </HudButton>
        </form>
      </HudPanel>

      {error && <HudError message={error} />}

      {memories.length === 0 && !error ? (
        <HudEmpty>No memories yet — tell Spidey to remember something.</HudEmpty>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {memories.map((m) => (
            <li key={m.id} className="hud-panel p-4">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm text-cyan-100/90">{m.content}</p>
                <button
                  onClick={() => handleDelete(m.id)}
                  title="Delete memory"
                  className="shrink-0 rounded-lg px-2 py-1 text-xs text-cyan-200/40 hover:bg-crimson/10 hover:text-red-300"
                >
                  ✕
                </button>
              </div>
              <div className="mt-3 flex items-center gap-3">
                <span className="rounded-full border border-violet-400/30 bg-violet-400/10 px-2 py-0.5 font-mono text-[11px] text-violet-300">
                  {m.category}
                </span>
                <div className="hud-meter flex-1">
                  <div style={{ width: `${Math.min(100, Math.max(0, m.importance * 100))}%` }} />
                </div>
                <span className="font-mono text-[11px] text-cyan-200/40">
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
