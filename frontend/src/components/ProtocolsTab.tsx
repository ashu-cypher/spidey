import { useEffect, useState } from 'react';
import { getProtocolAudit, getProtocols, triggerProtocol } from '../api';
import type { ProtocolAuditEntry, ProtocolDef } from '../api';
import { HudButton, HudEmpty, HudError, HudPanel, HudSelect, HudTitle } from './hud';
import { useJarvis } from '../jarvis/context';
import { playSfx } from '../audio/sfx';
import type { SfxName } from '../audio/sfx';

const CONFIRM_KEY = 'jarvis.confirmMode';
type ConfirmMode = 'always' | 'lowrisk-auto';

/** Map the backend's sfx tag to a synthesized SFX name (safe fallback: alert). */
function toSfxName(raw: string): SfxName {
  const known: SfxName[] = ['activation', 'blip', 'hum', 'chime', 'alert'];
  return (known as string[]).includes(raw) ? (raw as SfxName) : 'alert';
}

export function ProtocolsTab() {
  const { speak, flashMode, logTranscript, setReactorMode } = useJarvis();
  const [protocols, setProtocols] = useState<ProtocolDef[]>([]);
  const [audit, setAudit] = useState<ProtocolAuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [confirmMode, setConfirmMode] = useState<ConfirmMode>(() => {
    try {
      return localStorage.getItem(CONFIRM_KEY) === 'lowrisk-auto'
        ? 'lowrisk-auto'
        : 'always';
    } catch {
      return 'always';
    }
  });

  const refreshAudit = () => {
    getProtocolAudit()
      .then((data) => setAudit(data))
      .catch(() => {
        /* audit is best-effort */
      });
  };

  useEffect(() => {
    let cancelled = false;
    getProtocols()
      .then((data) => {
        if (!cancelled) {
          setProtocols(data);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Failed to load protocols');
      });
    refreshAudit();
    return () => {
      cancelled = true;
    };
  }, []);

  const changeConfirmMode = (mode: ConfirmMode) => {
    setConfirmMode(mode);
    try {
      localStorage.setItem(CONFIRM_KEY, mode);
    } catch {
      /* ignore */
    }
  };

  async function trigger(p: ProtocolDef) {
    if (triggering) return;
    setTriggering(p.id);
    try {
      const result = await triggerProtocol(p.id);
      flashMode('alert', 2000);
      playSfx(toSfxName(result.sfx));
      logTranscript('system', `Protocol engaged: ${result.name}`);
      speak(result.response_text);
      refreshAudit();
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Protocol trigger failed');
      setReactorMode('idle');
    } finally {
      setTriggering(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {error && <HudError message={error} />}

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <HudPanel title="Security protocols">
          {protocols.length === 0 && !error ? (
            <HudEmpty>No protocols registered on the backend yet.</HudEmpty>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2">
              {protocols.map((p) => (
                <li key={p.id} className="hud-panel flex flex-col !p-3">
                  <p className="font-mono text-sm font-bold uppercase tracking-[0.15em] text-crimson">
                    {p.name}
                  </p>
                  <p className="mt-1 flex-1 text-xs text-cyan-100/70">{p.description}</p>
                  <div className="mt-3">
                    <HudButton
                      variant="danger"
                      onClick={() => void trigger(p)}
                      disabled={triggering !== null}
                      className="w-full"
                    >
                      {triggering === p.id ? 'Engaging…' : 'Engage'}
                    </HudButton>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </HudPanel>

        <HudPanel title="Permission override">
          <p className="text-xs text-cyan-100/70">
            Confirmation policy for external / destructive actions.
          </p>
          <div className="mt-3">
            <HudSelect
              value={confirmMode}
              onChange={(e) => changeConfirmMode(e.target.value as ConfirmMode)}
              className="w-full"
              aria-label="Confirmation mode"
            >
              <option value="always">Always confirm</option>
              <option value="lowrisk-auto">Auto-approve low-risk</option>
            </HudSelect>
          </div>
          <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.15em] text-gold/80">
            Display preference — enforced server-side.
          </p>
        </HudPanel>
      </div>

      <div className="flex flex-col gap-2">
        <HudTitle>Engagement log ({audit.length})</HudTitle>
        {audit.length === 0 ? (
          <HudEmpty>No protocol engagements recorded.</HudEmpty>
        ) : (
          <ul className="space-y-2">
            {audit.map((a) => (
              <li key={a.id} className="hud-panel !p-3">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="rounded-full border border-crimson/40 bg-crimson/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em] text-red-300">
                    {a.protocol_name}
                  </span>
                  <span className="flex-1 truncate text-sm text-cyan-100/85">
                    {a.response_text}
                  </span>
                  <span className="font-mono text-[11px] text-cyan-200/40">
                    {new Date(a.triggered_at).toLocaleString()}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
