import { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { MewProvider, useMew, playSfx } from './mew/context';
import { HexGridBackground, HudPanel } from './components/hud';
import { MewView } from './components/MewView';
import { ActivityPanel } from './components/ActivityPanel';
import { LibraryTab } from './components/LibraryTab';
import { SettingsTab } from './components/SettingsTab';

// Heavy tabs load on first visit — MEW stays instant.
const TasksPanel = lazy(() =>
  import('./components/TasksPanel').then((m) => ({ default: m.TasksPanel })),
);
const RemindersPanel = lazy(() =>
  import('./components/RemindersPanel').then((m) => ({ default: m.RemindersPanel })),
);

type TabId = 'mew' | 'activity' | 'library' | 'tasks' | 'settings';

const TABS: { id: TabId; label: string }[] = [
  { id: 'mew', label: 'MEW' },
  { id: 'activity', label: 'Activity' },
  { id: 'library', label: 'Library' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'settings', label: 'Settings' },
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
  const { speak, flashMode, logTranscript, pushNotice } = useMew();
  const refs = useRef({ speak, flashMode, logTranscript, pushNotice });
  refs.current = { speak, flashMode, logTranscript, pushNotice };

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
          const { speak, flashMode, logTranscript, pushNotice } = refs.current;
          playSfx('alert');
          flashMode('alert', 3000);
          const text = `Reminder due: ${data.reminder.title}`;
          logTranscript('system', text);
          pushNotice(text);
          let proactive = true;
          try {
            proactive = localStorage.getItem('mew.proactiveVoice') !== '0';
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
          const text = `Protocol event: ${data.name}`;
          refs.current.logTranscript('system', text);
          refs.current.pushNotice(text);
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
  const { activityTick, activeTab, setActiveTab } = useMew();
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
              MEW
            </h1>
            <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.25em] text-cyan-200/50">
              Warm, direct, and a little playful
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="hud-pill hud-pill-on">
              <span className="inline-block h-2 w-2 rounded-full bg-emerald-400 hud-blink" />
              Systems nominal
            </span>
            <HudClock />
            <button
              type="button"
              onClick={() => {
                playSfx('blip');
                setActiveTab('settings');
              }}
              title="Settings"
              aria-label="Open settings"
              className={`flex h-9 w-9 items-center justify-center rounded-lg border text-base transition-all ${
                tab === 'settings'
                  ? 'border-accent/70 bg-accent/15 text-accent'
                  : 'border-accent/25 bg-carbon/60 text-cyan-200/60 hover:border-accent/50 hover:text-accent'
              }`}
            >
              ⚙
            </button>
          </div>
        </header>

        <nav className="mb-6 flex gap-2 overflow-x-auto pb-1" aria-label="Sections">
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
          <div hidden={tab !== 'mew'}>
            <MewView />
          </div>
          <div hidden={tab !== 'activity'}>
            <ActivityPanel refreshKey={activityTick} />
          </div>
          <div hidden={tab !== 'library'}>
            <LibraryTab />
          </div>
          <div hidden={tab !== 'tasks'}>
            <div className="flex flex-col gap-6">
              <Suspense fallback={<TabFallback />}>
                <TasksPanel />
                <RemindersPanel />
              </Suspense>
            </div>
          </div>
          <div hidden={tab !== 'settings'}>
            <SettingsTab />
          </div>
        </main>

        <footer className="mt-8 text-center font-mono text-[11px] uppercase tracking-[0.3em] text-cyan-200/30">
          MEW · online
        </footer>
      </div>
    </div>
  );
}

export function App() {
  return (
    <MewProvider>
      <Shell />
    </MewProvider>
  );
}
