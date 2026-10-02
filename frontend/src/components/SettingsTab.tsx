import { Suspense, lazy, useState } from 'react';
import type { ReactNode } from 'react';
import { HudPanel, HudTitle } from './hud';
import { MemoryPanel } from './MemoryPanel';
import { ProviderPicker } from './ProviderPicker';
import { VoiceTab } from './VoiceTab';
import {
  AppearanceSection,
  ConversationSection,
  PrivacySection,
} from './SettingsSections';

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
// Settings — the ONE secondary view. AI model/provider (+ model picker,
// wired to the backend's probe-validated provider API), voice + language,
// memory management, appearance, privacy, conversation management, plus
// System & Protocols as collapsible sections.
// ---------------------------------------------------------------------------

export function SettingsTab() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <HudTitle>Settings</HudTitle>

      <ProviderPicker />

      <HudPanel title="Voice & language">
        <VoiceTab />
      </HudPanel>

      <HudPanel title="Memory">
        <MemoryPanel />
      </HudPanel>

      <AppearanceSection />

      <PrivacySection />

      <ConversationSection />

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
