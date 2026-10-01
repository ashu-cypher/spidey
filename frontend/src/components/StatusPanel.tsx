import { useEffect, useState } from 'react';
import { getHealth, getMemories, getSystemMetrics, listDocuments } from '../api';
import { useJarvis } from '../jarvis/context';

// ---------------------------------------------------------------------------
// Status panel — REAL backend state only. Collapsed by default; expands on
// click. Polls /api/health + /api/system/metrics every 30 s; probes memory
// and knowledge once at mount. Never shows "Online" when a fetch fails.
// ---------------------------------------------------------------------------

type Probe = 'unknown' | 'ready' | 'unreachable';

interface Status {
  spidey: 'online' | 'degraded' | 'offline' | 'unknown';
  voice: Probe;
  memory: Probe;
  knowledge: Probe;
  tools: Probe;
  latencyMs: number | null;
}

function Dot({ ok }: { ok: boolean | null }) {
  const cls =
    ok === true
      ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]'
      : ok === false
        ? 'bg-crimson shadow-[0_0_8px_rgba(239,68,68,0.8)]'
        : 'bg-slate-500';
  return <span className={`inline-block h-2 w-2 rounded-full ${cls}`} />;
}

export function StatusPanel() {
  const { voiceSupported } = useJarvis();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status>({
    spidey: 'unknown',
    voice: 'unknown',
    memory: 'unknown',
    knowledge: 'unknown',
    tools: 'unknown',
    latencyMs: null,
  });

  // Lightweight poll: health + metrics every 30 s.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      const t0 = performance.now();
      let healthOk = false;
      let metricsOk = false;
      try {
        await getHealth();
        healthOk = true;
      } catch {
        healthOk = false;
      }
      try {
        await getSystemMetrics();
        metricsOk = true;
      } catch {
        metricsOk = false;
      }
      if (cancelled) return;
      setStatus((s) => ({
        ...s,
        spidey: healthOk ? (metricsOk ? 'online' : 'degraded') : 'offline',
        tools: metricsOk ? 'ready' : 'unreachable',
        latencyMs: healthOk ? Math.round(performance.now() - t0) : null,
      }));
    };
    void poll();
    const id = window.setInterval(() => void poll(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // One-shot probes at mount.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await getMemories();
        if (!cancelled) setStatus((s) => ({ ...s, memory: 'ready' }));
      } catch {
        if (!cancelled) setStatus((s) => ({ ...s, memory: 'unreachable' }));
      }
    })();
    void (async () => {
      try {
        await listDocuments();
        if (!cancelled) setStatus((s) => ({ ...s, knowledge: 'ready' }));
      } catch {
        if (!cancelled) setStatus((s) => ({ ...s, knowledge: 'unreachable' }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const voice: Probe = voiceSupported ? 'ready' : 'unreachable';

  const spideyLabel =
    status.spidey === 'online'
      ? 'Online'
      : status.spidey === 'degraded'
        ? 'Degraded'
        : status.spidey === 'offline'
          ? 'Offline'
          : '…';
  const spideyOk =
    status.spidey === 'online'
      ? true
      : status.spidey === 'unknown'
        ? null
        : false;

  const rows: { label: string; value: string; ok: boolean | null }[] = [
    { label: 'SPIDEY', value: spideyLabel, ok: spideyOk },
    {
      label: 'Voice',
      value: voice === 'ready' ? 'Ready' : 'Unavailable',
      ok: voice === 'ready',
    },
    {
      label: 'Memory',
      value: status.memory === 'ready' ? 'Ready' : status.memory === 'unreachable' ? 'Unreachable' : '…',
      ok: status.memory === 'ready' ? true : status.memory === 'unreachable' ? false : null,
    },
    {
      label: 'Knowledge',
      value: status.knowledge === 'ready' ? 'Ready' : status.knowledge === 'unreachable' ? 'Unreachable' : '…',
      ok: status.knowledge === 'ready' ? true : status.knowledge === 'unreachable' ? false : null,
    },
    {
      label: 'Tools',
      value: status.tools === 'ready' ? 'Ready' : status.tools === 'unreachable' ? 'Unreachable' : '…',
      ok: status.tools === 'ready' ? true : status.tools === 'unreachable' ? false : null,
    },
  ];

  return (
    <div className="w-full rounded-lg border border-accent/15 bg-carbon/60">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-3 py-2 font-mono text-[10px] uppercase tracking-[0.22em] text-cyan-200/60 hover:text-cyan-200"
      >
        <span className="flex items-center gap-2">
          <Dot ok={spideyOk} />
          SPIDEY · {spideyLabel}
          {status.latencyMs !== null && (
            <span className="text-cyan-200/30">{status.latencyMs}ms</span>
          )}
        </span>
        <span aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <ul className="space-y-1.5 border-t border-accent/10 px-3 py-2">
          {rows.map((r) => (
            <li
              key={r.label}
              className="flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.18em]"
            >
              <span className="flex items-center gap-2 text-cyan-200/50">
                <Dot ok={r.ok} />
                {r.label}
              </span>
              <span
                className={
                  r.ok === true
                    ? 'text-emerald-300/90'
                    : r.ok === false
                      ? 'text-red-300/90'
                      : 'text-slate-400'
                }
              >
                {r.value}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
