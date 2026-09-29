import { useEffect, useState } from 'react';
import { getTasks } from '../api';
import type { TaskItem } from '../api';

export function TasksPanel() {
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getTasks()
      .then((data) => {
        if (!cancelled) {
          setTasks(data);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load tasks');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <p className="text-sm text-red-400">{error}</p>;
  if (tasks.length === 0)
    return <p className="text-sm text-gray-500">No tasks yet — ask Spidey to track something for you.</p>;

  return (
    <ul className="space-y-2">
      {tasks.map((t) => (
        <li
          key={t.id}
          className="flex items-center gap-3 rounded-xl bg-panel border border-white/10 px-4 py-3"
        >
          <span className={`font-mono text-base ${t.done ? 'text-green-400' : 'text-gray-600'}`}>
            {t.done ? '✓' : '○'}
          </span>
          <span className={`flex-1 text-sm ${t.done ? 'text-gray-500 line-through' : 'text-gray-200'}`}>
            {t.title}
          </span>
          {t.due && (
            <span className="font-mono text-[11px] text-gray-500">
              {new Date(t.due).toLocaleDateString()}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
