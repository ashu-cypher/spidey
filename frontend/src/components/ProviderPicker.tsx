import { useEffect, useState } from 'react';
import { getSystemProvider, putSystemProvider } from '../api';
import { HudPanel } from './hud';

// ---------------------------------------------------------------------------
// ProviderPicker — model provider selection. GET /api/system/provider on
// mount, PUT /api/system/provider on change. Failures are shown honestly;
// the select never pretends a switch worked.
// ---------------------------------------------------------------------------

export function ProviderPicker() {
  const [providers, setProviders] = useState<string[]>([]);
  const [current, setCurrent] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const info = await getSystemProvider();
        if (cancelled) return;
        setProviders(info.providers ?? []);
        setCurrent(info.provider ?? '');
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error
            ? `Couldn't reach the provider endpoint: ${err.message}`
            : 'Provider endpoint unreachable — the backend may not support it yet.',
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onChange = (provider: string) => {
    setCurrent(provider);
    setSaving(true);
    setError(null);
    void putSystemProvider(provider)
      .then((info) => {
        setProviders(info.providers ?? []);
        setCurrent(info.provider ?? provider);
      })
      .catch((err: unknown) => {
        setError(
          err instanceof Error
            ? `Couldn't switch provider: ${err.message}`
            : 'Provider switch failed.',
        );
      })
      .finally(() => setSaving(false));
  };

  return (
    <HudPanel title="Model provider">
      {loading ? (
        <p className="font-mono text-xs uppercase tracking-[0.25em] text-cyan-200/40 hud-blink">
          Reading provider…
        </p>
      ) : error && providers.length === 0 ? (
        <p className="text-sm text-red-300/80">{error}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <label
            htmlFor="mew-provider"
            className="font-mono text-[11px] uppercase tracking-[0.22em] text-cyan-200/60"
          >
            Active provider
          </label>
          <select
            id="mew-provider"
            className="hud-input"
            value={current}
            disabled={saving}
            onChange={(e) => onChange(e.target.value)}
          >
            {providers.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          {saving && (
            <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-accent hud-blink">
              Switching…
            </span>
          )}
        </div>
      )}
      {error && providers.length > 0 && (
        <p className="mt-2 text-xs text-red-300/80">{error}</p>
      )}
      <p className="mt-2 text-xs text-cyan-200/40">
        Read from <span className="font-mono">GET /api/system/provider</span>;
        changes are applied with{' '}
        <span className="font-mono">PUT /api/system/provider</span>.
      </p>
    </HudPanel>
  );
}
