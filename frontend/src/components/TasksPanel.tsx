import { useEffect, useState } from 'react';
import { deleteTask, getTasks, setTaskDone } from '../api';
import type { TaskItem } from '../api';
import { HudEmpty, HudError, HudPanel } from './hud';

// Agent-managed list: the agent acts via conversation ("add a task…").
// This panel is for review — toggle done or delete. No input forms here.
export function TasksPanel() {
  const [tasks, setTasks] = useState<TaskItem[]>([]);
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
      <HudPanel title="Tasks">
        <p className="text-xs text-cyan-200/40">
          Managed by MEW — say “add a task to buy milk” in the conversation.
        </p>
      </HudPanel>

      {error && <HudError message={error} />}

      {tasks.length === 0 ? (
        <HudEmpty>No tasks yet — ask MEW to track something for you.</HudEmpty>
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
