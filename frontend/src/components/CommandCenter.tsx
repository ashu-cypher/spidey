import { useEffect, useRef, useState } from 'react';
import { ArcReactor } from './ArcReactor';
import { ChatPanel } from './ChatPanel';
import { Gauge, HudChip, HudEmpty, HudPanel } from './hud';
import { useJarvis } from '../jarvis/context';
import { getHealth, getSystemMetrics } from '../api';
import type { SystemMetrics } from '../api';

const QUICK_CHIPS = [
  'System status',
  'What am I working on?',
  'Search my documents for…',
  'Remind me in 10 minutes to…',
];

function formatUptime(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

function MicButton({ onClick, listening, supported }: { onClick: () => void; listening: boolean; supported: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!supported}
      title={supported ? (listening ? 'Stop listening' : 'Activate voice interface') : 'Voice not supported in this browser'}
      className={`relative mx-auto flex h-24 w-24 items-center justify-center rounded-full border-2 transition-all disabled:opacity-30 ${
        listening
          ? 'border-crimson/80 bg-crimson/10 shadow-[0_0_36px_rgba(239,68,68,0.45)]'
          : 'border-accent/60 bg-accent/5 shadow-[0_0_28px_rgba(0,240,255,0.3)] hover:bg-accent/15'
      }`}
    >
      {listening && (
        <span className="absolute inset-0 rounded-full border border-crimson/50 hud-blink" />
      )}
      <span className="text-3xl" aria-hidden="true">{listening ? '⏺' : '🎤'}</span>
    </button>
  );
}

function WakePill() {
  const { voiceSupported, listening, wakeMode } = useJarvis();
  if (!voiceSupported)
    return <span className="hud-pill hud-pill-off">Voice unsupported</span>;
  if (listening)
    return (
      <span className="hud-pill hud-pill-on">
        <span className="inline-block h-2 w-2 rounded-full bg-emerald-400 hud-blink" />
        Listening
      </span>
    );
  if (wakeMode === 'awake')
    return <span className="hud-pill hud-pill-on">Awake — 60s window</span>;
  return <span className="hud-pill hud-pill-off">Standby — say “Jarvis”</span>;
}

export function CommandCenter() {
  const {
    reactorMode,
    spectrumRef,
    transcript,
    clearTranscript,
    sendChat,
    toggleListening,
    listening,
    voiceSupported,
    chatBusy,
  } = useJarvis();

  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

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
        }
      } catch {
        /* keep last known values */
      }
    };
    void poll();
    const id = window.setInterval(() => void poll(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // Auto-scroll the live transcript.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [transcript]);

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 xl:grid-cols-[400px_1fr]">
        {/* Reactor + voice core */}
        <HudPanel title="Arc Reactor // Core Interface" className="flex flex-col items-center">
          <div className="hud-flicker">
            <ArcReactor mode={reactorMode} spectrum={spectrumRef} size={280} />
          </div>
          <div className="mt-2">
            <MicButton onClick={toggleListening} listening={listening} supported={voiceSupported} />
          </div>
          <p className="mt-3 font-mono text-[11px] uppercase tracking-[0.25em] text-cyan-200/50">
            Voice interface
          </p>
          <div className="mt-2">
            <WakePill />
          </div>
          <div className="mt-5 w-full border-t border-accent/10 pt-4">
            <p className="hud-subtitle mb-2">Tactical prompts</p>
            <div className="flex flex-wrap gap-2">
              {QUICK_CHIPS.map((chip) => (
                <HudChip key={chip} onClick={() => sendChat(chip)} disabled={chatBusy}>
                  {chip}
                </HudChip>
              ))}
            </div>
          </div>
        </HudPanel>

        {/* Gauges + live transcript */}
        <div className="flex min-w-0 flex-col gap-4">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Gauge
              label="CPU load"
              value={metrics ? metrics.cpu_percent.toFixed(0) : '—'}
              unit="%"
              percent={metrics?.cpu_percent ?? 0}
            />
            <Gauge
              label="Memory"
              value={metrics ? metrics.ram.percent.toFixed(0) : '—'}
              unit="%"
              percent={metrics?.ram.percent ?? 0}
            />
            <Gauge
              label="Latency"
              value={latency !== null ? String(latency) : '—'}
              unit="ms"
              percent={latency !== null ? Math.min(100, (latency / 500) * 100) : 0}
              warnAt={60}
              dangerAt={90}
            />
            <Gauge
              label="Uptime"
              value={metrics ? formatUptime(metrics.uptime_seconds) : '—'}
              percent={100}
              warnAt={101}
              dangerAt={101}
            />
          </div>

          <HudPanel
            title="Live transcript"
            className="flex min-h-[240px] flex-1 flex-col"
            right={
              transcript.length > 0 ? (
                <button
                  type="button"
                  onClick={clearTranscript}
                  className="font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-200/40 hover:text-cyan-200"
                >
                  Clear
                </button>
              ) : undefined
            }
          >
            <div ref={logRef} className="max-h-[300px] flex-1 space-y-2 overflow-y-auto pr-1">
              {transcript.length === 0 ? (
                <HudEmpty>
                  No voice traffic yet. Tap the mic or say “Jarvis” while listening.
                </HudEmpty>
              ) : (
                transcript.map((line) => (
                  <div key={line.id} className="flex gap-2 text-sm">
                    <span className="shrink-0 font-mono text-[10px] text-cyan-200/30">
                      {line.at}
                    </span>
                    <span
                      className={`font-mono text-[10px] uppercase tracking-widest ${
                        line.role === 'user'
                          ? 'text-accent'
                          : line.role === 'jarvis'
                            ? 'text-gold'
                            : 'text-slate-500'
                      }`}
                    >
                      {line.role === 'user' ? 'YOU' : line.role === 'jarvis' ? 'JARVIS' : 'SYS'}
                    </span>
                    <span className="text-cyan-100/85">{line.text}</span>
                  </div>
                ))
              )}
            </div>
          </HudPanel>
        </div>
      </div>

      {/* Full chat console */}
      <ChatPanel />
    </div>
  );
}
