import { useState } from 'react';
import { HudButton, HudPanel, HudToggle } from './hud';
import { useMew } from '../mew/context';

// ---------------------------------------------------------------------------
// Settings sections for the single-conversation MEW UI:
//  - AppearanceSection: reduce-motion + hide-avatar, persisted locally and
//    applied to <html> immediately (real, no fake preview).
//  - PrivacySection: honest rundown of what is stored where, plus a real
//    "clear local browser data" action.
//  - ConversationSection: conversation management — start a fresh
//    conversation from Settings (wired to MewView via context).
// ---------------------------------------------------------------------------

const REDUCE_MOTION_KEY = 'mew.reduceMotion';
const HIDE_AVATAR_KEY = 'mew.hideAvatar';

/** Browser-local data MEW keeps (keys only — values never leave the device). */
const LOCAL_KEYS = [
  'mew.conversationId',
  'mew.voiceLang',
  'mew.volume',
  'mew.voiceURI',
  'mew.pitch',
  'mew.rate',
  'mew.proactiveVoice',
  'mew.sfx',
  REDUCE_MOTION_KEY,
  HIDE_AVATAR_KEY,
];

function readFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}

/** Apply the persisted appearance prefs to the document. Called at boot. */
export function applyAppearance(): void {
  try {
    const root = document.documentElement;
    root.classList.toggle('mew-reduced-motion', readFlag(REDUCE_MOTION_KEY));
    root.classList.toggle('mew-hide-avatar', readFlag(HIDE_AVATAR_KEY));
  } catch {
    /* storage unavailable — leave defaults */
  }
}

export function AppearanceSection() {
  const [reduceMotion, setReduceMotion] = useState(() => readFlag(REDUCE_MOTION_KEY));
  const [hideAvatar, setHideAvatar] = useState(() => readFlag(HIDE_AVATAR_KEY));

  const toggle = (key: string, set: (v: boolean) => void) => (v: boolean) => {
    set(v);
    try {
      localStorage.setItem(key, v ? '1' : '0');
    } catch {
      /* ignore */
    }
    applyAppearance();
  };

  return (
    <HudPanel title="Appearance">
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm text-cyan-100/85">Reduce motion</p>
            <p className="text-xs text-cyan-200/40">
              Disables avatar animation, pulses, and transitions.
            </p>
          </div>
          <HudToggle
            on={reduceMotion}
            onChange={toggle(REDUCE_MOTION_KEY, setReduceMotion)}
            label="Reduce motion"
          />
        </div>
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm text-cyan-100/85">Compact chat</p>
            <p className="text-xs text-cyan-200/40">
              Hides the spider avatar for a pure conversation view.
            </p>
          </div>
          <HudToggle
            on={hideAvatar}
            onChange={toggle(HIDE_AVATAR_KEY, setHideAvatar)}
            label="Compact chat"
          />
        </div>
      </div>
    </HudPanel>
  );
}

export function PrivacySection() {
  const [cleared, setCleared] = useState(false);

  const clearLocal = () => {
    try {
      for (const k of LOCAL_KEYS) localStorage.removeItem(k);
    } catch {
      /* ignore */
    }
    applyAppearance();
    setCleared(true);
    window.setTimeout(() => setCleared(false), 2500);
  };

  return (
    <HudPanel title="Privacy">
      <ul className="list-disc space-y-1.5 pl-5 text-xs text-cyan-100/70">
        <li>
          The microphone is only active inside an explicitly enabled
          conversation mode — never in the background.
        </li>
        <li>
          Preferences (voice, language, appearance) stay in this
          browser&apos;s local storage.
        </li>
        <li>
          Conversations, memories, tasks, reminders, and documents live on
          your MEW backend (the machine serving this page) — not in the
          cloud.
        </li>
        <li>
          The AI provider choice is stored server-side in{' '}
          <span className="font-mono">backend/.provider.json</span>; API keys
          stay in the server&apos;s <span className="font-mono">.env</span> and
          are never sent to the browser.
        </li>
      </ul>
      <div className="mt-3 flex items-center gap-3">
        <HudButton onClick={clearLocal} title="Remove MEW's browser-local data">
          Clear local browser data
        </HudButton>
        {cleared && (
          <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-emerald-300">
            Cleared
          </span>
        )}
      </div>
    </HudPanel>
  );
}

export function ConversationSection() {
  const { requestClearConversation } = useMew();
  const [conversationId, setConversationId] = useState<string>(() => {
    try {
      return localStorage.getItem('mew.conversationId') ?? '';
    } catch {
      return '';
    }
  });

  const startFresh = () => {
    requestClearConversation();
    // The id rotates when MewView clears; re-read it shortly after.
    window.setTimeout(() => {
      try {
        setConversationId(localStorage.getItem('mew.conversationId') ?? '');
      } catch {
        /* ignore */
      }
    }, 100);
  };

  return (
    <HudPanel title="Conversation">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-cyan-100/85">Start a fresh conversation</p>
          <p className="text-xs text-cyan-200/40">
            Clears messages, history, and attachment context — MEW forgets
            this thread.
          </p>
          {conversationId && (
            <p className="mt-1 font-mono text-[10px] text-cyan-200/30">
              thread {conversationId.slice(0, 8)}…
            </p>
          )}
        </div>
        <HudButton onClick={startFresh} title="Clear the current conversation">
          New conversation
        </HudButton>
      </div>
    </HudPanel>
  );
}
