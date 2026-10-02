import { useEffect, useState } from 'react';
import { HudPanel } from './hud';

const API = '';

async function api(path: string, opts?: RequestInit) {
  const r = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  return r.json();
}

export function TelegramConfig() {
  const [status, setStatus] = useState<{ configured: boolean; state: string } | null>(null);
  const [botToken, setBotToken] = useState('');
  const [chatId, setChatId] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const s = await api('/api/system/status');
      setStatus(s.telegram || null);
    } catch {
      setStatus(null);
    }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy(true);
    setMsg('');
    try {
      const r = await api('/api/system/telegram/config', {
        method: 'POST',
        body: JSON.stringify({ bot_token: botToken, chat_id: chatId }),
      });
      setMsg(r.message || (r.ok ? 'Saved.' : 'Failed.'));
      if (r.ok) {
        setBotToken('');
        setChatId('');
        load();
      }
    } catch {
      setMsg('Could not reach the backend.');
    }
    setBusy(false);
  };

  const test = async () => {
    setBusy(true);
    setMsg('');
    try {
      // Test the form values if provided, else the stored config.
      const r = await api('/api/system/telegram/test', {
        method: 'POST',
        body: JSON.stringify(
          botToken ? { bot_token: botToken, chat_id: chatId } : {}
        ),
      });
      setMsg(r.message || (r.ok ? 'Connected.' : 'Failed.'));
    } catch {
      setMsg('Could not reach the backend.');
    }
    setBusy(false);
  };

  const stateLabel = !status
    ? 'UNKNOWN'
    : status.state === 'connected'
      ? 'READY'
      : status.configured
        ? 'CONFIGURED'
        : 'NOT CONFIGURED';

  return (
    <HudPanel title="Telegram reminders">
      <div className="flex flex-col gap-3 text-sm">
        <div>
          Status: <span className="font-mono">{stateLabel}</span>
        </div>
        <p className="opacity-70">
          Create a bot with @BotFather on Telegram, message it once, then paste
          the token and your chat ID below. Reminders will be delivered to
          Telegram when due.
        </p>
        <input
          type="password"
          value={botToken}
          onChange={(e) => setBotToken(e.target.value)}
          placeholder="Bot token (from @BotFather)"
          className="rounded bg-black/40 px-3 py-2 font-mono text-xs"
          autoComplete="off"
        />
        <input
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
          placeholder="Chat ID (message @userinfobot to get yours)"
          className="rounded bg-black/40 px-3 py-2 font-mono text-xs"
          autoComplete="off"
        />
        <div className="flex gap-2">
          <button
            onClick={save}
            disabled={busy || !botToken || !chatId}
            className="rounded bg-red-600 px-4 py-2 text-xs font-bold disabled:opacity-40"
          >
            {busy ? '…' : 'Save'}
          </button>
          <button
            onClick={test}
            disabled={busy}
            className="rounded border border-white/20 px-4 py-2 text-xs disabled:opacity-40"
          >
            Test connection
          </button>
        </div>
        {msg && <div className="text-xs opacity-80">{msg}</div>}
      </div>
    </HudPanel>
  );
}
