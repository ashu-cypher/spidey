import { useEffect, useState } from 'react';
import { getSystemProvider, listOllamaModels, putSystemProvider } from '../api';
import { HudButton, HudPanel } from './hud';

// ---------------------------------------------------------------------------
// ProviderPicker — AI provider + model selection.
// GET /api/system/provider → { provider, model, available_providers } on
// mount; PUT /api/system/provider ← { provider, model } on Apply. The
// backend probes the candidate first, so an unreachable Ollama/OpenAI (or a
// missing model) comes back as a human-readable 502 and nothing is
// persisted. For Ollama, GET /api/system/models offers the live model list
// as suggestions (falls back to free text if the endpoint/backend is not
// there yet). Failures are shown honestly; the UI never pretends a switch
// worked.
// ---------------------------------------------------------------------------

export function ProviderPicker() {
  const [providers, setProviders] = useState<string[]>([]);
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [savedModel, setSavedModel] = useState<string | null>(null);
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [ollamaNote, setOllamaNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const info = await getSystemProvider();
        if (cancelled) return;
        setProviders(info.available_providers ?? []);
        setProvider(info.provider ?? '');
        setModel(info.model ?? '');
        setSavedModel(info.model ?? null);
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

  // Live Ollama model suggestions (graceful: free text still works).
  useEffect(() => {
    if (provider !== 'ollama') {
      setOllamaModels([]);
      setOllamaNote(null);
      return;
    }
    let cancelled = false;
    setOllamaNote(null);
    void (async () => {
      try {
        const info = await listOllamaModels();
        if (!cancelled) setOllamaModels(info.models ?? []);
      } catch (err) {
        if (!cancelled) {
          setOllamaModels([]);
          setOllamaNote(
            err instanceof Error
              ? `Live model list unavailable: ${err.message}`
              : 'Live model list unavailable — type the model name; Apply will probe it.',
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [provider]);

  const dirty =
    savedModel === null ? model.trim() !== '' : model.trim() !== (savedModel ?? '');

  const apply = () => {
    if (!provider || saving) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    const wantedModel = model.trim() || null;
    void putSystemProvider(provider, wantedModel)
      .then((info) => {
        setProviders(info.available_providers ?? []);
        setProvider(info.provider ?? provider);
        setModel(info.model ?? '');
        setSavedModel(info.model ?? null);
        setSaved(true);
      })
      .catch((err: unknown) => {
        // Probe failure: the backend explains why (e.g. Ollama not running,
        // model not pulled). Show it verbatim — never a traceback.
        setError(
          err instanceof Error
            ? `Couldn't switch provider: ${err.message}`
            : 'Provider switch failed.',
        );
      })
      .finally(() => setSaving(false));
  };

  return (
    <HudPanel title="AI model">
      {loading ? (
        <p className="font-mono text-xs uppercase tracking-[0.25em] text-cyan-200/40 hud-blink">
          Reading provider…
        </p>
      ) : error && providers.length === 0 ? (
        <p className="text-sm text-red-300/80">{error}</p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <label
              htmlFor="mew-provider"
              className="font-mono text-[11px] uppercase tracking-[0.22em] text-cyan-200/60"
            >
              Provider
            </label>
            <select
              id="mew-provider"
              className="hud-input"
              value={provider}
              disabled={saving}
              onChange={(e) => {
                setProvider(e.target.value);
                setSaved(false);
              }}
            >
              {providers.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label
              htmlFor="mew-model"
              className="font-mono text-[11px] uppercase tracking-[0.22em] text-cyan-200/60"
            >
              Model
            </label>
            <input
              id="mew-model"
              className="hud-input min-w-0 flex-1"
              value={model}
              disabled={saving}
              placeholder="provider default"
              spellCheck={false}
              list={ollamaModels.length > 0 ? 'mew-ollama-models' : undefined}
              onChange={(e) => {
                setModel(e.target.value);
                setSaved(false);
              }}
              title="Model name for this provider (e.g. qwen3:0.6b for Ollama). Empty = provider default."
            />
            {ollamaModels.length > 0 && (
              <datalist id="mew-ollama-models">
                {ollamaModels.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            )}
          </div>
          {ollamaNote && (
            <p className="text-xs text-cyan-200/40">{ollamaNote}</p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <HudButton
              variant="primary"
              onClick={apply}
              disabled={saving || !provider || (!dirty && saved)}
              title="Probe this provider + model, then save"
            >
              {saving ? 'Probing…' : 'Apply'}
            </HudButton>
            {saved && !dirty && (
              <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-emerald-300">
                ✓ Active
              </span>
            )}
            {saving && (
              <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-accent hud-blink">
                Probing…
              </span>
            )}
          </div>
        </div>
      )}
      {error && providers.length > 0 && (
        <p className="mt-2 text-xs text-red-300/80">{error}</p>
      )}
      <p className="mt-2 text-xs text-cyan-200/40">
        Read from <span className="font-mono">GET /api/system/provider</span>;
        applied with <span className="font-mono">PUT /api/system/provider</span>{' '}
        after the backend probes the model. Leave the model empty for the
        provider default.
      </p>
    </HudPanel>
  );
}
