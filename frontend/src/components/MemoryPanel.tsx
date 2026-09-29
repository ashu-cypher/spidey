import { useEffect, useState } from 'react';
import { getMemories } from '../api';
import type { MemoryItem } from '../api';

export function MemoryPanel() {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMemories()
      .then((data) => {
        if (!cancelled) {
          setMemories(data);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load memories');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <p className="text-sm text-red-400">{error}</p>;
  if (memories.length === 0)
    return <p className="text-sm text-gray-500">No memories yet — tell Spidey to remember something.</p>;

  return (
    <ul className="grid gap-3 md:grid-cols-2">
      {memories.map((m) => (
        <li key={m.id} className="rounded-xl bg-panel border border-white/10 p-4">
          <p className="text-sm text-gray-200">{m.content}</p>
          <div className="mt-3 flex items-center gap-3">
            <span className="rounded-full bg-accent2/10 border border-accent2/30 px-2 py-0.5 font-mono text-[11px] text-accent2">
              {m.category}
            </span>
            <div className="flex-1 h-1.5 rounded-full bg-white/5">
              <div
                className="h-1.5 rounded-full bg-gradient-to-r from-accent to-accent2"
                style={{ width: `${Math.min(100, Math.max(0, m.importance * 10))}%` }}
              />
            </div>
            <span className="font-mono text-[11px] text-gray-500">
              {new Date(m.created_at).toLocaleDateString()}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
