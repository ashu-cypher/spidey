import { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { JarvisProvider, useJarvis, playSfx } from './jarvis/context';
import { HexGridBackground, HudPanel } from './components/hud';
import { CommandCenter } from './components/CommandCenter';
import { ActivityPanel } from './components/ActivityPanel';

// Heavy tabs load on first visit — Command Center stays instant.
const SystemTab = lazy(() =>
  import('./components/SystemTab').then((m) => ({ default: m.SystemTab })),
);
const ProtocolsTab = lazy(() =>
  import('./components/ProtocolsTab').then((m) => ({ default: m.ProtocolsTab })),
);
const MemoryPanel = lazy(() =>
  import('./components/MemoryPanel').then((m) => ({ default: m.MemoryPanel })),
);
const KnowledgePanel = lazy(() =>
  import('./components/KnowledgePanel').then((m) => ({ default: m.KnowledgePanel })),
);
const ResumePanel = lazy(() =>
  import('./components/ResumePanel').then((m) => ({ default: m.ResumePanel })),
);
const TasksPanel = lazy(() =>
  import('./components/TasksPanel').then((m) => ({ default: m.TasksPanel })),
);
const RemindersPanel = lazy(() =>
  import('./components/RemindersPanel').then((m) => ({ default: m.RemindersPanel })),
);
const VoiceTab = lazy(() =>
  import('./components/VoiceTab').then((m) => ({ default: m.VoiceTab })),
);

type TabId =
  | 'command'
  | 'workflows'
  | 'system'
  | 'security'
  | 'knowledge'
  | 'tasks'
  | 'voice';

const TABS: { id: TabId; label: string }[] = [
  { id: 'command', label: 'Command Center' },
  { id: 'workflows', label: 'Workflows & Logs' },
  { id: 'system', label: 'System & Diagnostics' },
  { id: 'security', label: 'Security & Protocols' },
  { id: 'knowledge', label: 'Knowledge & Memory' },
  { id: 'tasks', label: 'Tasks & Missions' },
  { id: 'voice', label: 'Voice & Persona' },
];

function TabFallback() {
  return (
    <HudPanel>
      <p className="font-mono text-xs uppercase tracking-[0.25em] text-cyan-200/40 hud-blink">
        Loading module…
      </p>
    </HudPanel>
  );
}

/** Global SSE event bus: /api/events/stream with reconnect backoff. */
function useGlobalEvents() {
  const { speak, flashMode, logTranscript } = useJarvis();
  const refs = useRef({ speak, flashMode, logTranscript });
  refs.current = { speak, flashMode, logTranscript };

  useEffect(() => {
    let es: EventSource | null = null;
    let timer: number | null = null;
    let closed = false;
    let retries = 0;

    const connect = () => {
      if (closed) return;
      es = new EventSource('/api/events/stream');

      es.addEventListener('reminder_due', (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as {
            type: string;
            reminder: { id: string; title: string; remind_at: string };
          };
          const { speak, flashMode, logTranscript } = refs.current;
          playSfx('alert');
          flashMode('alert', 3000);
          logTranscript('system', `Reminder due: ${data.reminder.title}`);
          let proactive = true;
          try {
            proactive = localStorage.getItem('jarvis.proactiveVoice') !== '0';
          } catch {
            /* default on */
          }
          if (proactive) speak(`Sir, reminder: ${data.reminder.title}`);
        } catch {
          /* malformed event — ignore */
        }
      });

      es.addEventListener('protocol', (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as {
            type: string;
            id: string;
            name: string;
          };
          refs.current.logTranscript('system', `Protocol event: ${data.name}`);
        } catch {
          /* ignore */
        }
        refs.current.flashMode('alert', 1500);
        playSfx('blip');
      });

      es.onopen = () => {
        retries = 0;
      };

      es.onerror = () => {
        es?.close();
        if (closed) return;
        retries += 1;
        const backoff = Math.min(30000, 1000 * 2 ** Math.min(retries, 6));
        timer = window.setTimeout(connect, backoff);
      };
    };

    connect();
    return () => {
      closed = true;
      es?.close();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, []);
}

function HudClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <span className="font-mono text-xs tracking-[0.25em] text-cyan-200/60">
      {now.toLocaleTimeString('en-GB', { hour12: false })}
    </span>
  );
}

function Shell() {
  const { activityTick, activeTab, setActiveTab } = useJarvis();
  const tab = activeTab as TabId;
  useGlobalEvents();

  return (
    <div className="relative min-h-screen">
      <HexGridBackground />
      <div className="hud-scanlines" aria-hidden="true" />
      <div className="hud-vignette" aria-hidden="true" />
      {/* slow sweeping scan bar */}
      <div className="pointer-events-none fixed inset-x-0 top-0 z-[91] h-24 overflow-hidden" aria-hidden="true">
        <div className="hud-sweep-line h-16 w-full bg-gradient-to-b from-transparent via-accent/[0.04] to-transparent" />
      </div>

      <div className="relative z-10 mx-auto max-w-7xl px-4 py-6">
        <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="glow font-mono text-3xl font-bold uppercase tracking-[0.3em] text-white">
              J.A.R.V.I.S.
            </h1>
            <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.25em] text-cyan-200/50">
              Just A Rather Very Intelligent System
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="hud-pill hud-pill-on">
              <span className="inline-block h-2 w-2 rounded-full bg-emerald-400 hud-blink" />
              Systems nominal
            </span>
            <HudClock />
          </div>
        </header>

        <nav className="mb-6 flex gap-2 overflow-x-auto pb-1" aria-label="Command sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                playSfx('blip');
                setActiveTab(t.id);
              }}
              className={`shrink-0 px-4 py-2.5 font-mono text-[11px] uppercase tracking-[0.22em] transition-all ${
                tab === t.id
                  ? 'hud-btn hud-btn-primary'
                  : 'hud-btn opacity-70 hover:opacity-100'
              }`}
              aria-current={tab === t.id ? 'page' : undefined}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <main>
          {/* All sections stay mounted so voice, chat, and polling persist. */}
          <div hidden={tab !== 'command'}>
            <CommandCenter />
          </div>
          <div hidden={tab !== 'workflows'}>
            <ActivityPanel refreshKey={activityTick} />
          </div>
          <div hidden={tab !== 'system'}>
            <Suspense fallback={<TabFallback />}>
              <SystemTab />
            </Suspense>
          </div>
          <div hidden={tab !== 'security'}>
            <Suspense fallback={<TabFallback />}>
              <ProtocolsTab />
            </Suspense>
          </div>
          <div hidden={tab !== 'knowledge'}>
            <div className="flex flex-col gap-6">
              <Suspense fallback={<TabFallback />}>
                <MemoryPanel />
                <KnowledgePanel />
                <HudPanel title="Resume intelligence">
                  <ResumePanel />
                </HudPanel>
              </Suspense>
            </div>
          </div>
          <div hidden={tab !== 'tasks'}>
            <div className="flex flex-col gap-6">
              <Suspense fallback={<TabFallback />}>
                <TasksPanel />
                <RemindersPanel />
              </Suspense>
            </div>
          </div>
          <div hidden={tab !== 'voice'}>
            <Suspense fallback={<TabFallback />}>
              <VoiceTab />
            </Suspense>
          </div>
        </main>

        <footer className="mt-8 text-center font-mono text-[11px] uppercase tracking-[0.3em] text-cyan-200/30">
          Stark HUD interface · J.A.R.V.I.S. online
        </footer>
      </div>
    </div>
  );
}

export function App() {
  return (
    <JarvisProvider>
      <Shell />
    </JarvisProvider>
  );
}
