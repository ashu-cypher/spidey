import { Suspense, lazy, useState } from 'react';
import type { ReactNode } from 'react';
import { HudPanel, HudTitle } from './hud';
import { MemoryPanel } from './MemoryPanel';
import { VoiceTab } from './VoiceTab';

const SystemTab = lazy(() =>
  import('./SystemTab').then((m) => ({ default: m.SystemTab })),
);
const ProtocolsTab = lazy(() =>
  import('./ProtocolsTab').then((m) => ({ default: m.ProtocolsTab })),
);

function Collapsible({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <HudPanel title={title}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between font-mono text-[11px] uppercase tracking-[0.22em] text-cyan-200/60 hover:text-cyan-200"
      >
        <span>
          {open ? '▾' : '▸'} {open ? 'Hide' : 'Show'} {title.toLowerCase()}
        </span>
      </button>
      {open && <div className="mt-4">{children}</div>}
      {!open && (
        <p className="mt-2 text-xs text-cyan-200/40">
          You can also drive these by talking to MEW — try “system status” or
          “sentry mode”.
        </p>
      )}
    </HudPanel>
  );
}

// ---------------------------------------------------------------------------
// Settings — voice settings (incl. English/हिंदी/Hinglish/Auto), persona,
// memory management, plus System & Protocols as collapsible sections.
// ---------------------------------------------------------------------------

export function SettingsTab() {
  return (
    <div className="flex flex-col gap-6">
      <HudTitle>Settings</HudTitle>

      <HudPanel title="Voice & persona">
        <VoiceTab />
      </HudPanel>

      <HudPanel title="Memory management">
        <MemoryPanel />
      </HudPanel>

      <Suspense
        fallback={
          <HudPanel>
            <p className="font-mono text-xs uppercase tracking-[0.25em] text-cyan-200/40 hud-blink">
              Loading module…
            </p>
          </HudPanel>
        }
      >
        <Collapsible title="System & diagnostics">
          <SystemTab />
        </Collapsible>
        <Collapsible title="Security & protocols">
          <ProtocolsTab />
        </Collapsible>
      </Suspense>
    </div>
  );
}
