import { useEffect, useState } from 'react';
import { createTask, deleteTask, getTasks, setTaskDone } from '../api';
import type { TaskItem } from '../api';
import { HudButton, HudEmpty, HudError, HudInput, HudPanel } from './hud';

export function TasksPanel() {
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [title, setTitle] = useState('');
  const [due, setDue] = useState('');
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setTasks(await getTasks());
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load tasks');
    }
  }

  useEffect(() => {
    let cancelled = false;
    getTasks()
      .then((data) => {
        if (!cancelled) setTasks(data);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Failed to load tasks');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const t = title.trim();
    if (!t) return;
    try {
      await createTask(t, due || null);
      setTitle('');
      setDue('');
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create task');
    }
  }

  async function toggle(t: TaskItem) {
    try {
      await setTaskDone(t.id, !t.done);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to update task');
    }
  }

  async function remove(id: string) {
    try {
      await deleteTask(id);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to delete task');
    }
  }

  return (
    <div className="space-y-4">
      <HudPanel title="New objective">
        <form onSubmit={add} className="flex flex-wrap gap-2">
          <HudInput
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="New task…"
            className="min-w-[180px] flex-1"
          />
          <HudInput
            type="date"
            value={due}
            onChange={(e) => setDue(e.target.value)}
            title="Due date"
          />
          <HudButton type="submit" variant="primary" disabled={!title.trim()}>
            Deploy
          </HudButton>
        </form>
      </HudPanel>

      {error && <HudError message={error} />}

      {tasks.length === 0 ? (
        <HudEmpty>No tasks yet — ask Spidey to track something for you.</HudEmpty>
      ) : (
        <ul className="space-y-2">
          {tasks.map((t) => (
            <li
              key={t.id}
              className="hud-panel flex items-center gap-3 !p-3 px-4"
            >
              <button
                type="button"
                onClick={() => void toggle(t)}
                title={t.done ? 'Reopen' : 'Mark done'}
                className={`font-mono text-base ${
                  t.done ? 'text-emerald-400' : 'text-cyan-200/30 hover:text-accent'
                }`}
              >
                {t.done ? '✓' : '○'}
              </button>
              <span
                className={`flex-1 text-sm ${
                  t.done ? 'text-cyan-200/30 line-through' : 'text-cyan-100/90'
                }`}
              >
                {t.title}
              </span>
              {t.due && (
                <span className="font-mono text-[11px] text-cyan-200/40">
                  {new Date(t.due).toLocaleDateString()}
                </span>
              )}
              <button
                type="button"
                onClick={() => void remove(t.id)}
                title="Delete task"
                className="px-1 text-sm text-cyan-200/30 hover:text-red-300"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
