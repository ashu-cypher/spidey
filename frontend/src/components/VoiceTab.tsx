import { useEffect, useState } from 'react';
import { getHealth } from '../api';
import type { Health } from '../api';
import { HudButton, HudChip, HudError, HudPanel, HudSelect, HudToggle } from './hud';
import { useMew } from '../mew/context';
import { listVoices, pickBritishVoice } from '../voice/mewVoice';
import type { VoiceLangSetting } from '../voice/mewVoice';
import { playSfx } from '../audio/sfx';
import type { SfxName } from '../audio/sfx';

const PITCH_KEY = 'mew.pitch';
const RATE_KEY = 'mew.rate';
const VOICE_URI_KEY = 'mew.voiceURI';
const PROACTIVE_KEY = 'mew.proactiveVoice';

const SFX_TESTS: { name: SfxName; label: string }[] = [
  { name: 'activation', label: 'Activation' },
  { name: 'blip', label: 'Blip' },
  { name: 'hum', label: 'Hum' },
  { name: 'chime', label: 'Chime' },
  { name: 'alert', label: 'Alert' },
];

const LANG_OPTIONS: { value: VoiceLangSetting; label: string }[] = [
  { value: 'auto', label: 'Auto (default)' },
  { value: 'en', label: 'English' },
  { value: 'hi', label: 'हिंदी' },
  { value: 'hinglish', label: 'Hinglish' },
];

function readNum(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key);
    const n = raw === null ? NaN : parseFloat(raw);
    return Number.isNaN(n) ? fallback : Math.min(2, Math.max(0.5, n));
  } catch {
    return fallback;
  }
}

function isBritish(v: SpeechSynthesisVoice): boolean {
  return (
    v.name.includes('Google UK English Male') ||
    v.name.includes('Daniel') ||
    v.lang.toLowerCase().startsWith('en-gb')
  );
}

export function VoiceTab() {
  const {
    voice,
    voiceSupported,
    ttsSupported,
    sfxEnabled,
    setSfxEnabledState,
    speak,
    wakeMode,
    listening,
    voiceLang,
    setVoiceLang,
    volume,
    setVolume,
  } = useMew();

  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [voiceURI, setVoiceURI] = useState<string>(() => {
    try {
      return localStorage.getItem(VOICE_URI_KEY) ?? '';
    } catch {
      return '';
    }
  });
  const [pitch, setPitch] = useState(() => readNum(PITCH_KEY, 1));
  const [rate, setRate] = useState(() => readNum(RATE_KEY, 1));
  const [proactive, setProactive] = useState<boolean>(() => {
    try {
      return localStorage.getItem(PROACTIVE_KEY) !== '0';
    } catch {
      return true;
    }
  });
  const [sensitivity, setSensitivity] = useState<'lenient' | 'strict'>(() =>
    voice.getSensitivity(),
  );
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  // Voice list (Chrome loads voices asynchronously).
  useEffect(() => {
    if (!ttsSupported) return;
    const load = () => setVoices(listVoices());
    load();
    window.speechSynthesis.addEventListener('voiceschanged', load);
    return () => window.speechSynthesis.removeEventListener('voiceschanged', load);
  }, [ttsSupported]);

  useEffect(() => {
    let cancelled = false;
    getHealth()
      .then((data) => {
        if (!cancelled) {
          setHealth(data);
          setHealthError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setHealthError(err instanceof Error ? err.message : 'Backend unreachable');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const persist = (key: string, value: string) => {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* ignore */
    }
  };

  const changeVoice = (uri: string) => {
    setVoiceURI(uri);
    persist(VOICE_URI_KEY, uri);
  };

  const changePitch = (v: number) => {
    setPitch(v);
    persist(PITCH_KEY, String(v));
  };

  const changeRate = (v: number) => {
    setRate(v);
    persist(RATE_KEY, String(v));
  };

  const changeProactive = (v: boolean) => {
    setProactive(v);
    persist(PROACTIVE_KEY, v ? '1' : '0');
  };

  const selectedVoice = voices.find((v) => v.voiceURI === voiceURI) ?? null;
  const effectiveVoice = selectedVoice ?? pickBritishVoice(voices);

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="flex flex-col gap-4">
        {/* Persona */}
        <HudPanel title="Persona">
          <p className="font-mono text-lg font-bold tracking-[0.1em] text-accent glow">
            MEW
          </p>
          <p className="mt-1 text-sm text-cyan-100/80">
            Warm, direct, and a little playful
          </p>
          <p className="mt-2 font-mono text-xs uppercase tracking-[0.2em] text-gold/90">
            Addressing you as: sir
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <span className={`hud-pill ${voiceSupported ? 'hud-pill-on' : 'hud-pill-off'}`}>
              Recognition {voiceSupported ? 'online' : 'offline'}
            </span>
            <span className={`hud-pill ${ttsSupported ? 'hud-pill-on' : 'hud-pill-off'}`}>
              Speech {ttsSupported ? 'online' : 'offline'}
            </span>
            <span className={`hud-pill ${listening ? 'hud-pill-on' : 'hud-pill-off'}`}>
              {listening ? 'Listening' : 'Mic idle'}
            </span>
            <span className="hud-pill hud-pill-off">Wake: {wakeMode}</span>
          </div>
        </HudPanel>

        {/* Language */}
        <HudPanel title="Language">
          <label className="hud-subtitle mb-2 block" htmlFor="voice-lang-select">
            Voice language
          </label>
          <HudSelect
            id="voice-lang-select"
            value={voiceLang}
            onChange={(e) => setVoiceLang(e.target.value as VoiceLangSetting)}
            className="w-full"
            disabled={!voiceSupported}
          >
            {LANG_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </HudSelect>
          <p className="mt-2 text-xs text-cyan-200/50">
            Auto follows your last detected language; browsers can&apos;t do
            true dual-language recognition, so Hinglish and Auto listen in
            English and switch the spoken voice by the reply&apos;s language.
            Hindi replies prefer a Hindi voice when one is installed.
          </p>
        </HudPanel>

        {/* Voice selection */}
        <HudPanel title="Voice matrix">
          {!ttsSupported ? (
            <p className="text-sm text-cyan-200/40">
              Speech synthesis is not available in this browser.
            </p>
          ) : (
            <div className="flex flex-col gap-4">
              <div>
                <label className="hud-subtitle mb-2 block" htmlFor="voice-select">
                  Voice
                </label>
                <HudSelect
                  id="voice-select"
                  value={voiceURI}
                  onChange={(e) => changeVoice(e.target.value)}
                  className="w-full"
                >
                  <option value="">Auto — British preference</option>
                  {voices.map((v) => (
                    <option key={v.voiceURI} value={v.voiceURI}>
                      {isBritish(v) ? '🇬🇧 ' : ''}{v.name} ({v.lang})
                      {v.localService ? ' · local' : ''}
                    </option>
                  ))}
                </HudSelect>
                <p className="mt-2 text-xs text-cyan-200/50">
                  Active: {effectiveVoice ? `${effectiveVoice.name} (${effectiveVoice.lang})` : 'system default'}
                </p>
              </div>

              <div>
                <label className="hud-subtitle mb-2 block" htmlFor="pitch">
                  Pitch — {pitch.toFixed(2)}
                </label>
                <input
                  id="pitch"
                  type="range"
                  min={0.5}
                  max={2}
                  step={0.05}
                  value={pitch}
                  onChange={(e) => changePitch(parseFloat(e.target.value))}
                  className="hud-range"
                />
              </div>

              <div>
                <label className="hud-subtitle mb-2 block" htmlFor="rate">
                  Rate — {rate.toFixed(2)}
                </label>
                <input
                  id="rate"
                  type="range"
                  min={0.5}
                  max={2}
                  step={0.05}
                  value={rate}
                  onChange={(e) => changeRate(parseFloat(e.target.value))}
                  className="hud-range"
                />
              </div>

              <div>
                <label className="hud-subtitle mb-2 block" htmlFor="volume">
                  Volume — {Math.round(volume * 100)}%
                </label>
                <input
                  id="volume"
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={volume}
                  onChange={(e) => setVolume(parseFloat(e.target.value))}
                  className="hud-range"
                />
              </div>

              <div>
                <HudButton
                  onClick={() => speak('Systems online, sir. All interfaces nominal.')}
                  disabled={!ttsSupported}
                >
                  Test voice
                </HudButton>
              </div>
            </div>
          )}
        </HudPanel>

        {/* Wake word */}
        <HudPanel title="Wake-word protocol">
          <label className="hud-subtitle mb-2 block" htmlFor="wake-sensitivity">
            Sensitivity
          </label>
          <HudSelect
            id="wake-sensitivity"
            value={sensitivity}
            onChange={(e) => {
              const s = e.target.value as 'lenient' | 'strict';
              setSensitivity(s);
              voice.setSensitivity(s);
            }}
            className="w-full"
            disabled={!voiceSupported}
          >
            <option value="lenient">Lenient — any “mew”</option>
            <option value="strict">Strict — phrase must start with “mew”</option>
          </HudSelect>
          <p className="mt-2 text-xs text-cyan-200/50">
            While sleeping, only the wake word activates MEW Manual mic
            activation grants a 60-second awake window.
          </p>
        </HudPanel>
      </div>

      <div className="flex flex-col gap-4">
        {/* SFX */}
        <HudPanel title="Sound effects">
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm text-cyan-100/85">Synthesized HUD SFX</span>
            <HudToggle on={sfxEnabled} onChange={setSfxEnabledState} label="SFX enabled" />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {SFX_TESTS.map((s) => (
              <HudChip key={s.name} onClick={() => playSfx(s.name)}>
                ▶ {s.label}
              </HudChip>
            ))}
          </div>
        </HudPanel>

        {/* Proactive voice */}
        <HudPanel title="Proactive announcements">
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm text-cyan-100/85">
              Speak reminders aloud when they fire
            </span>
            <HudToggle on={proactive} onChange={changeProactive} label="Proactive voice" />
          </div>
          <p className="mt-2 text-xs text-cyan-200/50">
            “Sir, reminder: …” — disable for silent HUD alerts only.
          </p>
        </HudPanel>

        {/* Connection (legacy settings folded in) */}
        <HudPanel title="Uplink">
          {healthError ? (
            <HudError message={healthError} />
          ) : health ? (
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-cyan-200/40">Status</dt>
                <dd className="font-mono text-emerald-400">{health.status}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-cyan-200/40">Provider</dt>
                <dd className="font-mono text-cyan-100/90">{health.provider}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-cyan-200/40">Backend</dt>
                <dd className="font-mono text-cyan-100/90">http://127.0.0.1:8000</dd>
              </div>
            </dl>
          ) : (
            <p className="hud-blink text-sm text-cyan-200/40">Establishing uplink…</p>
          )}
        </HudPanel>
      </div>
    </div>
  );
}
