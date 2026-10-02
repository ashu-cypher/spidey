import { useEffect, useState } from 'react';
import {
  deleteReminder,
  deleteTask,
  getReminders,
  getTasks,
  setReminderDone,
  setTaskDone,
} from '../api';
import type { ReminderItem, TaskItem } from '../api';
import { HudError } from './hud';

// ---------------------------------------------------------------------------
// Inline task/reminder cards — the old Tasks tab's review list, rendered
// inline in the conversation right under the reply whose tool run produced
// it. Every row is live: toggle done/reopen and delete hit the real APIs.
// ---------------------------------------------------------------------------

function ToggleBtn({
  done,
  onClick,
  title,
}: {
  done: boolean;
  onClick: () => void;
  title: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      className={`shrink-0 font-mono text-base ${
        done ? 'text-emerald-400' : 'text-cyan-200/30 hover:text-accent'
      }`}
    >
      {done ? '✓' : '○'}
    </button>
  );
}

function DeleteBtn({ onClick, title }: { onClick: () => void; title: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      className="shrink-0 px-1 text-sm text-cyan-200/30 hover:text-red-300"
    >
      ✕
    </button>
  );
}

function CardShell({
  icon,
  title,
  hint,
  children,
}: {
  icon: string;
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-2 overflow-hidden rounded-xl border border-accent/20 bg-carbon/70">
      <div className="flex items-center gap-2 border-b border-accent/10 px-3 py-2">
        <span aria-hidden="true" className="text-sm">
          {icon}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-cyan-200/70">
          {title}
        </span>
      </div>
      <div className="px-3 py-2">{children}</div>
      <p className="px-3 pb-2 text-[11px] text-cyan-200/35">{hint}</p>
    </div>
  );
}

/** Live task list card for the conversation. */
export function TaskListCard() {
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
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Failed to load tasks');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = async () => {
    try {
      setTasks(await getTasks());
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load tasks');
    }
  };

  const toggle = async (t: TaskItem) => {
    try {
      await setTaskDone(t.id, !t.done);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to update task');
    }
  };

  const remove = async (id: string) => {
    try {
      await deleteTask(id);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to delete task');
    }
  };

  return (
    <CardShell
      icon="✓"
      title="Tasks"
      hint="Managed by MEW — say “add a task…” or “mark … done” in the conversation."
    >
      {error && <HudError message={error} />}
      {tasks.length === 0 && !error ? (
        <p className="py-1 text-xs text-cyan-200/40">
          No tasks — ask MEW to track something for you.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {tasks.map((t) => (
            <li key={t.id} className="flex items-center gap-2.5">
              <ToggleBtn
                done={t.done}
                onClick={() => void toggle(t)}
                title={t.done ? 'Reopen task' : 'Mark task done'}
              />
              <span
                className={`flex-1 text-[13px] ${
                  t.done ? 'text-cyan-200/30 line-through' : 'text-cyan-100/90'
                }`}
              >
                {t.title}
              </span>
              {t.due && (
                <span className="font-mono text-[10px] text-cyan-200/40">
                  {new Date(t.due).toLocaleDateString()}
                </span>
              )}
              <DeleteBtn
                onClick={() => void remove(t.id)}
                title="Delete task"
              />
            </li>
          ))}
        </ul>
      )}
    </CardShell>
  );
}

/** Live reminder list card for the conversation. */
export function ReminderListCard() {
  const [reminders, setReminders] = useState<ReminderItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getReminders()
      .then((data) => {
        if (!cancelled) {
          setReminders(data);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(
            err instanceof Error ? err.message : 'Failed to load reminders',
          );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = async () => {
    try {
      setReminders(await getReminders());
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load reminders');
    }
  };

  const toggle = async (r: ReminderItem) => {
    try {
      await setReminderDone(r.id, !r.done);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to update reminder');
    }
  };

  const remove = async (id: string) => {
    try {
      await deleteReminder(id);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to delete reminder');
    }
  };

  return (
    <CardShell
      icon="🔔"
      title="Reminders"
      hint="Managed by MEW — say “remind me…” in the conversation."
    >
      {error && <HudError message={error} />}
      {reminders.length === 0 && !error ? (
        <p className="py-1 text-xs text-cyan-200/40">
          No reminders — ask MEW to remind you of something.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {reminders.map((r) => (
            <li key={r.id} className="flex items-center gap-2.5">
              <ToggleBtn
                done={r.done}
                onClick={() => void toggle(r)}
                title={r.done ? 'Reopen reminder' : 'Mark reminder done'}
              />
              <span
                className={`flex-1 text-[13px] ${
                  r.done ? 'text-cyan-200/30 line-through' : 'text-cyan-100/90'
                }`}
              >
                {r.title}
              </span>
              {r.remind_at && (
                <span className="font-mono text-[10px] text-cyan-200/40">
                  {new Date(r.remind_at).toLocaleString()}
                </span>
              )}
              <DeleteBtn
                onClick={() => void remove(r.id)}
                title="Delete reminder"
              />
            </li>
          ))}
        </ul>
      )}
    </CardShell>
  );
}
