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

export function WhatsAppConfig() {
  const [status, setStatus] = useState<{ configured: boolean; state: string } | null>(null);
  const [token, setToken] = useState('');
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [recipient, setRecipient] = useState('');
  const [verifyToken, setVerifyToken] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const s = await api('/api/system/status');
      setStatus(s.whatsapp || null);
    } catch {
      setStatus(null);
    }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy(true);
    setMsg('');
    try {
      const r = await api('/api/system/whatsapp/config', {
        method: 'POST',
        body: JSON.stringify({
          token,
          phone_number_id: phoneNumberId,
          recipient,
          verify_token: verifyToken,
          app_secret: appSecret,
        }),
      });
      setMsg(r.message || (r.ok ? 'Saved.' : 'Failed.'));
      if (r.ok) {
        setToken('');
        setPhoneNumberId('');
        setRecipient('');
        setVerifyToken('');
        setAppSecret('');
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
      const r = await api('/api/system/whatsapp/test', {
        method: 'POST',
        body: JSON.stringify(
          token
            ? {
                token,
                phone_number_id: phoneNumberId,
                recipient,
              }
            : {}
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
    : status.state === 'configured'
      ? 'READY'
      : status.configured
        ? 'CONFIGURED'
        : 'NOT CONFIGURED';

  return (
    <HudPanel title="WhatsApp">
      <div className="flex flex-col gap-3 text-sm">
        <div>
          Status: <span className="font-mono">{stateLabel}</span>
        </div>
        <p className="opacity-70">
          Sends reminders and chat replies over WhatsApp via Meta's WhatsApp
          Cloud API. Set this up yourself in the Meta dashboard:
        </p>
        <ol className="list-decimal space-y-1 pl-5 text-xs opacity-70">
          <li>Create an app at developers.facebook.com.</li>
          <li>Add the WhatsApp product to the app.</li>
          <li>Get a test number, or add your own business number.</li>
          <li>
            Copy the permanent access token and the phone number ID from the
            WhatsApp → API Setup page.
          </li>
          <li>
            Set the webhook URL to{' '}
            <span className="font-mono">
              https://&lt;your-public-backend&gt;/api/whatsapp/webhook
            </span>{' '}
            with a verify token you choose (paste it below too).
          </li>
          <li>Subscribe the webhook to the “messages” field.</li>
          <li>Paste the token, phone number ID, your number, verify token and app secret below, then Save.</li>
        </ol>
        <p className="text-xs opacity-70">
          Honest note: receiving WhatsApp messages needs a publicly reachable
          HTTPS URL (e.g. ngrok or a Cloudflare tunnel on your machine) —
          Meta can't reach a localhost backend. MEW cannot receive WhatsApp
          messages without it, but CAN send once the token is saved.
        </p>
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Access token (permanent, from Meta dashboard)"
          className="rounded bg-black/40 px-3 py-2 font-mono text-xs"
          autoComplete="off"
        />
        <input
          value={phoneNumberId}
          onChange={(e) => setPhoneNumberId(e.target.value)}
          placeholder="Phone number ID (WhatsApp → API Setup)"
          className="rounded bg-black/40 px-3 py-2 font-mono text-xs"
          autoComplete="off"
        />
        <input
          value={recipient}
          onChange={(e) => setRecipient(e.target.value)}
          placeholder="Your WhatsApp number (with country code, e.g. 9198…)"
          className="rounded bg-black/40 px-3 py-2 font-mono text-xs"
          autoComplete="off"
        />
        <input
          type="password"
          value={verifyToken}
          onChange={(e) => setVerifyToken(e.target.value)}
          placeholder="Verify token (you choose it; same as webhook setup)"
          className="rounded bg-black/40 px-3 py-2 font-mono text-xs"
          autoComplete="off"
        />
        <input
          type="password"
          value={appSecret}
          onChange={(e) => setAppSecret(e.target.value)}
          placeholder="App secret (App Settings → Basic; for signature check)"
          className="rounded bg-black/40 px-3 py-2 font-mono text-xs"
          autoComplete="off"
        />
        <div className="flex gap-2">
          <button
            onClick={save}
            disabled={busy || !token || !phoneNumberId || !recipient}
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
