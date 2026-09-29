import { useEffect, useState } from 'react';
import { getHealth } from '../api';
import type { Health } from '../api';

const BACKEND_URL = 'http://127.0.0.1:8000';

const ROADMAP = [
  'Phase 2 — Memory + PostgreSQL',
  'Phase 3 — RAG with pgvector',
  'Phase 4 — Resume intelligence',
  'Phase 5 — Tools',
  'Phase 6 — Voice',
  'Phase 7 — Advanced agent',
];

export function SettingsPanel() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getHealth()
      .then((data) => {
        if (!cancelled) {
          setHealth(data);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Backend unreachable');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-4 max-w-2xl">
      <div className="rounded-xl bg-panel border border-white/10 p-4">
        <h2 className="mb-3 font-mono text-xs tracking-widest text-accent">CONNECTION</h2>
        {error ? (
          <p className="text-sm text-red-400">{error}</p>
        ) : health ? (
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-gray-500">Status</dt>
              <dd className="font-mono text-green-400">{health.status}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Provider</dt>
              <dd className="font-mono text-gray-200">{health.provider}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Backend URL</dt>
              <dd className="font-mono text-gray-200">{BACKEND_URL}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Phase</dt>
              <dd className="font-mono text-gray-200">1 (core)</dd>
            </div>
          </dl>
        ) : (
          <p className="text-sm text-gray-500 animate-pulse">Checking backend health…</p>
        )}
      </div>

      <div className="rounded-xl bg-panel border border-white/10 p-4">
        <h2 className="mb-3 font-mono text-xs tracking-widest text-accent">ROADMAP</h2>
        <ul className="space-y-2">
          <li className="flex items-center gap-2 text-sm text-gray-200">
            <span className="text-green-400">✓</span> Phase 1 — Core chat + workflow streaming
          </li>
          {ROADMAP.map((item) => (
            <li key={item} className="flex items-center gap-2 text-sm text-gray-500">
              <span className="text-gray-600">○</span> {item}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
