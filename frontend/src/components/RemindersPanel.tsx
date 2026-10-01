import { useEffect, useState } from 'react';
import {
  createReminder,
  deleteReminder,
  getReminders,
  setReminderDone,
} from '../api';
import type { ReminderItem } from '../api';
import { HudButton, HudEmpty, HudError, HudInput, HudPanel } from './hud';

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
      <HudPanel title="Schedule reminder">
        <form onSubmit={add} className="flex flex-wrap gap-2">
          <HudInput
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Remind me to…"
            className="min-w-[180px] flex-1"
          />
          <HudInput
            type="datetime-local"
            value={when}
            onChange={(e) => setWhen(e.target.value)}
            title="Remind at"
          />
          <HudButton type="submit" variant="primary" disabled={!title.trim()}>
            Arm
          </HudButton>
        </form>
      </HudPanel>

      {error && <HudError message={error} />}

      {reminders.length === 0 ? (
        <HudEmpty>No reminders yet — try “remind me to call mom tomorrow”.</HudEmpty>
      ) : (
        <ul className="space-y-2">
          {reminders.map((r) => (
            <li
              key={r.id}
              className="hud-panel flex items-center gap-3 !p-3 px-4"
            >
              <button
                type="button"
                onClick={() => void toggle(r)}
                title={r.done ? 'Reopen' : 'Mark done'}
                className={`font-mono text-base ${
                  r.done ? 'text-emerald-400' : 'text-cyan-200/30 hover:text-accent'
                }`}
              >
                {r.done ? '✓' : '○'}
              </button>
              <span
                className={`flex-1 text-sm ${
                  r.done ? 'text-cyan-200/30 line-through' : 'text-cyan-100/90'
                }`}
              >
                {r.title}
              </span>
              {r.remind_at && (
                <span className="font-mono text-[11px] text-cyan-200/40">
                  {new Date(r.remind_at).toLocaleString()}
                </span>
              )}
              <button
                type="button"
                onClick={() => void remove(r.id)}
                title="Delete reminder"
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
