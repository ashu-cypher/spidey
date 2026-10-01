import { useEffect, useState } from 'react';
import { getHealth, getSystemMetrics } from '../api';
import type { SystemMetrics } from '../api';
import { Gauge, HudEmpty, HudError, HudPanel } from './hud';

const DAEMONS = [
  { name: 'Reminder scheduler', detail: '30s tick' },
  { name: 'Event stream', detail: 'SSE · /api/events/stream' },
  { name: 'Workflow engine', detail: 'orchestrator pipeline' },
];

function formatUptime(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${s % 60}s`;
}

interface BatteryState {
  percent: number;
  plugged: boolean;
}

export function SystemTab() {
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [battery, setBattery] = useState<BatteryState | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Telemetry poll (5 s).
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const t0 = performance.now();
        const [m] = await Promise.all([getSystemMetrics(), getHealth()]);
        const ping = Math.round(performance.now() - t0);
        if (!cancelled) {
          setMetrics(m);
          setLatency(ping);
          setError(null);
        }
      } catch (err: unknown) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Telemetry unreachable');
      }
    };
    void poll();
    const id = window.setInterval(() => void poll(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // Local battery (browser API, guarded — desktop browsers often lack it).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (!('getBattery' in navigator)) return;
        const nav = navigator as Navigator & {
          getBattery?: () => Promise<{ level: number; charging: boolean }>;
        };
        if (!nav.getBattery) return;
        const b = await nav.getBattery();
        if (!cancelled)
          setBattery({ percent: Math.round(b.level * 100), plugged: b.charging });
      } catch {
        /* battery API unavailable — ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const batteryView = metrics?.battery ?? battery;

  return (
    <div className="flex flex-col gap-4">
      {error && <HudError message={error} />}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Gauge
          label="CPU load"
          value={metrics ? metrics.cpu_percent.toFixed(1) : '—'}
          unit="%"
          percent={metrics?.cpu_percent ?? 0}
        />
        <Gauge
          label="Memory"
          value={
            metrics
              ? `${metrics.ram.used_gb.toFixed(1)} / ${metrics.ram.total_gb.toFixed(1)}`
              : '—'
          }
          unit={metrics ? 'GB' : ''}
          percent={metrics?.ram.percent ?? 0}
        />
        <Gauge
          label="Disk"
          value={metrics ? metrics.disk.percent.toFixed(0) : '—'}
          unit="%"
          percent={metrics?.disk.percent ?? 0}
          warnAt={80}
          dangerAt={95}
        />
        <Gauge
          label="Network latency"
          value={latency !== null ? String(latency) : '—'}
          unit="ms"
          percent={latency !== null ? Math.min(100, (latency / 500) * 100) : 0}
          warnAt={60}
          dangerAt={90}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <HudPanel title="Host">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between gap-2">
              <dt className="text-cyan-200/40">Hostname</dt>
              <dd className="truncate font-mono text-cyan-100/90">{metrics?.host ?? '—'}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-cyan-200/40">Uptime</dt>
              <dd className="font-mono text-cyan-100/90">
                {metrics ? formatUptime(metrics.uptime_seconds) : '—'}
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-cyan-200/40">Backend</dt>
              <dd className="font-mono text-cyan-100/90">127.0.0.1:8000</dd>
            </div>
          </dl>
        </HudPanel>

        <HudPanel title="Power">
          {batteryView ? (
            <div>
              <div className="font-mono text-2xl font-bold text-accent">
                {batteryView.percent}
                <span className="ml-1 text-sm font-normal opacity-70">%</span>
              </div>
              <div className="hud-meter mt-2">
                <div style={{ width: `${batteryView.percent}%` }} />
              </div>
              <p className="mt-2 font-mono text-[11px] uppercase tracking-[0.2em] text-cyan-200/50">
                {batteryView.plugged ? '⚡ Charging' : '▮ On battery'}
              </p>
            </div>
          ) : (
            <HudEmpty>No battery telemetry reported.</HudEmpty>
          )}
        </HudPanel>

        <HudPanel title="Daemons">
          <ul className="space-y-2">
            {DAEMONS.map((d) => (
              <li key={d.name} className="flex items-center gap-2 text-sm">
                <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.9)]" />
                <span className="flex-1 text-cyan-100/90">{d.name}</span>
                <span className="font-mono text-[11px] text-cyan-200/40">{d.detail}</span>
              </li>
            ))}
          </ul>
        </HudPanel>
      </div>
    </div>
  );
}
