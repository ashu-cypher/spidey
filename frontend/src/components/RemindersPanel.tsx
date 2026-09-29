import { useEffect, useState } from 'react';
import {
  createReminder,
  deleteReminder,
  getReminders,
  setReminderDone,
} from '../api';
import type { ReminderItem } from '../api';

export function RemindersPanel() {
  const [reminders, setReminders] = useState<ReminderItem[]>([]);
  const [title, setTitle] = useState('');
  const [when, setWhen] = useState('');
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setReminders(await getReminders());
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load reminders');
    }
  }

  useEffect(() => {
    let cancelled = false;
    getReminders()
      .then((data) => {
        if (!cancelled) setReminders(data);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Failed to load reminders');
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
      await createReminder(t, when || null);
      setTitle('');
      setWhen('');
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create reminder');
    }
  }

  async function toggle(r: ReminderItem) {
    try {
      await setReminderDone(r.id, !r.done);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to update reminder');
    }
  }

  async function remove(id: string) {
    try {
      await deleteReminder(id);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to delete reminder');
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={add} className="flex flex-wrap gap-2">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Remind me to…"
          className="flex-1 min-w-[180px] rounded-lg bg-void border border-white/10 px-4 py-2 text-sm text-gray-100 placeholder-gray-600 outline-none focus:border-accent/60"
        />
        <input
          type="datetime-local"
          value={when}
          onChange={(e) => setWhen(e.target.value)}
          title="Remind at"
          className="rounded-lg bg-void border border-white/10 px-3 py-2 text-sm text-gray-300 outline-none focus:border-accent/60"
        />
        <button
          type="submit"
          disabled={!title.trim()}
          className="rounded-lg bg-accent/20 border border-accent/40 px-4 py-2 text-sm font-medium text-accent hover:bg-accent/30 disabled:opacity-40"
        >
          Set
        </button>
      </form>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {reminders.length === 0 ? (
        <p className="text-sm text-gray-500">
          No reminders yet — try “remind me to call mom tomorrow”.
        </p>
      ) : (
        <ul className="space-y-2">
          {reminders.map((r) => (
            <li
              key={r.id}
              className="flex items-center gap-3 rounded-xl bg-panel border border-white/10 px-4 py-3"
            >
              <button
                type="button"
                onClick={() => void toggle(r)}
                title={r.done ? 'Reopen' : 'Mark done'}
                className={`font-mono text-base ${
                  r.done ? 'text-green-400' : 'text-gray-600 hover:text-accent'
                }`}
              >
                {r.done ? '✓' : '○'}
              </button>
              <span
                className={`flex-1 text-sm ${
                  r.done ? 'text-gray-500 line-through' : 'text-gray-200'
                }`}
              >
                {r.title}
              </span>
              {r.remind_at && (
                <span className="font-mono text-[11px] text-gray-500">
                  {new Date(r.remind_at).toLocaleString()}
                </span>
              )}
              <button
                type="button"
                onClick={() => void remove(r.id)}
                title="Delete reminder"
                className="text-gray-600 hover:text-red-400 text-sm px-1"
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
