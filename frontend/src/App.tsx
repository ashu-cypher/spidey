import { useState } from 'react';
import { ActivityPanel } from './components/ActivityPanel';
import { ChatPanel } from './components/ChatPanel';
import { KnowledgePanel } from './components/KnowledgePanel';
import { MemoryPanel } from './components/MemoryPanel';
import { ResumePanel } from './components/ResumePanel';
import { SettingsPanel } from './components/SettingsPanel';
import { TasksPanel } from './components/TasksPanel';

type Tab = 'chat' | 'activity' | 'knowledge' | 'resume' | 'memory' | 'tasks' | 'settings';

const TABS: { id: Tab; label: string }[] = [
  { id: 'chat', label: 'Chat' },
  { id: 'activity', label: 'Activity' },
  { id: 'knowledge', label: 'Knowledge' },
  { id: 'resume', label: 'Resume' },
  { id: 'memory', label: 'Memory' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'settings', label: 'Settings' },
];

export function App() {
  const [tab, setTab] = useState<Tab>('chat');
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="min-h-screen bg-void text-gray-100">
      <div className="mx-auto max-w-6xl px-4 py-6">
        <header className="mb-6">
          <h1 className="glow font-mono text-3xl font-bold tracking-tight text-white">
            SPIDEY <span className="text-accent">/</span>
          </h1>
          <p className="mt-1 text-sm text-gray-400">Your Personal AI Assistant</p>
        </header>

        <nav className="mb-6 flex gap-1 rounded-xl bg-panel border border-white/10 p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`flex-1 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                tab === t.id
                  ? 'bg-accent/15 text-accent border border-accent/30'
                  : 'text-gray-400 border border-transparent hover:text-gray-200 hover:bg-white/5'
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <main>
          {tab === 'chat' && <ChatPanel onActivity={() => setRefreshKey((k) => k + 1)} />}
          {tab === 'activity' && <ActivityPanel refreshKey={refreshKey} />}
          {tab === 'knowledge' && <KnowledgePanel />}
          {tab === 'resume' && <ResumePanel />}
          {tab === 'memory' && <MemoryPanel />}
          {tab === 'tasks' && <TasksPanel />}
          {tab === 'settings' && <SettingsPanel />}
        </main>

        <footer className="mt-8 text-center font-mono text-[11px] text-gray-600">
          SPIDEY — Phase 4 Resume
        </footer>
      </div>
    </div>
  );
}
