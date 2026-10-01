import { useEffect, useRef } from 'react';

// ---------------------------------------------------------------------------
// J.A.R.V.I.S. — synthesized SFX engine (Web Audio API, zero assets).
// A single lazily-created AudioContext feeds a master gain + persistent
// AnalyserNode so the Arc Reactor can visualize output energy.
// ---------------------------------------------------------------------------

export type SfxName = 'activation' | 'blip' | 'hum' | 'chime' | 'alert';

const SFX_KEY = 'jarvis.sfx';

let ctx: AudioContext | null = null;
let master: GainNode | null = null;
let analyser: AnalyserNode | null = null;

function ensureContext(): AudioContext | null {
  if (typeof window === 'undefined') return null;
  if (ctx) {
    if (ctx.state === 'suspended') void ctx.resume().catch(() => undefined);
    return ctx;
  }
  const AC =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext;
  if (!AC) return null;
  ctx = new AC();
  master = ctx.createGain();
  master.gain.value = 0.5;
  analyser = ctx.createAnalyser();
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.8;
  master.connect(analyser);
  analyser.connect(ctx.destination);
  return ctx;
}

/** Call from a user gesture at least once so the context can resume. */
export function unlockAudio(): void {
  ensureContext();
}

export function isSfxEnabled(): boolean {
  try {
    return localStorage.getItem(SFX_KEY) !== '0';
  } catch {
    return true;
  }
}

export function setSfxEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(SFX_KEY, enabled ? '1' : '0');
  } catch {
    /* storage unavailable — ignore */
  }
}

/** Persistent analyser hooked to the master gain; null until first unlock. */
export function getAnalyser(): AnalyserNode | null {
  return analyser;
}

interface ToneOpts {
  freq: number;
  freqEnd?: number;
  dur: number;
  type: OscillatorType;
  gain?: number;
  delay?: number;
  filterFreq?: number;
}

function tone(opts: ToneOpts): void {
  const ac = ensureContext();
  if (!ac || !master || !isSfxEnabled()) return;
  const t0 = ac.currentTime + (opts.delay ?? 0);
  const osc = ac.createOscillator();
  osc.type = opts.type;
  osc.frequency.setValueAtTime(opts.freq, t0);
  if (opts.freqEnd !== undefined) {
    osc.frequency.exponentialRampToValueAtTime(Math.max(1, opts.freqEnd), t0 + opts.dur);
  }
  const g = ac.createGain();
  const peak = opts.gain ?? 0.25;
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(peak, t0 + 0.012);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + opts.dur);
  if (opts.filterFreq) {
    const lp = ac.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = opts.filterFreq;
    osc.connect(g);
    g.connect(lp);
    lp.connect(master);
  } else {
    osc.connect(g);
    g.connect(master);
  }
  osc.start(t0);
  osc.stop(t0 + opts.dur + 0.05);
}

/** Play a synthesized SFX. No-op when SFX are disabled or audio is locked. */
export function playSfx(name: SfxName): void {
  if (!isSfxEnabled()) return;
  switch (name) {
    case 'activation':
      // Startup beep: sine 880 -> 1320 Hz sweep, 0.15s
      tone({ freq: 880, freqEnd: 1320, dur: 0.15, type: 'sine', gain: 0.3 });
      break;
    case 'blip':
      // HUD blip: short square 1200 Hz, 0.05s
      tone({ freq: 1200, dur: 0.05, type: 'square', gain: 0.12 });
      break;
    case 'hum':
      // Processing hum: sawtooth 110 Hz through lowpass, 0.8s
      tone({
        freq: 110,
        dur: 0.8,
        type: 'sawtooth',
        gain: 0.16,
        filterFreq: 420,
      });
      break;
    case 'chime':
      // Completion chime: two sines 660 + 990 Hz
      tone({ freq: 660, dur: 0.22, type: 'sine', gain: 0.28 });
      tone({ freq: 990, dur: 0.3, type: 'sine', gain: 0.24, delay: 0.09 });
      break;
    case 'alert':
      // Crimson alert: square 220 Hz x3 pulses
      tone({ freq: 220, dur: 0.12, type: 'square', gain: 0.2 });
      tone({ freq: 220, dur: 0.12, type: 'square', gain: 0.2, delay: 0.16 });
      tone({ freq: 220, dur: 0.16, type: 'square', gain: 0.22, delay: 0.32 });
      break;
  }
}

/**
 * rAF-driven pseudo/live spectrum for the Arc Reactor.
 *
 * Blends real analyser byte-frequency data (when the AudioContext exists)
 * with animated shimmer so the reactor always breathes. When `active` is
 * true (listening), energy is boosted. Returns a stable ref — the reactor
 * reads `.current` imperatively in its own rAF loop, so no React re-renders.
 */
export function useSpectrum(active: boolean, bins = 64): { current: number[] } {
  const dataRef = useRef<number[]>(new Array<number>(bins).fill(0));
  const tmpRef = useRef<Uint8Array | null>(null);

  useEffect(() => {
    let raf = 0;
    let t = Math.random() * 100;
    const loop = () => {
      t += 0.06;
      const out = dataRef.current;
      const an = getAnalyser();
      let live: Uint8Array | null = null;
      if (an) {
        if (!tmpRef.current || tmpRef.current.length !== an.frequencyBinCount) {
          tmpRef.current = new Uint8Array(an.frequencyBinCount);
        }
        an.getByteFrequencyData(tmpRef.current);
        live = tmpRef.current;
      }
      for (let i = 0; i < bins; i += 1) {
        const liveVal = live
          ? live[Math.floor((i / bins) * live.length)] / 255
          : 0;
        const shimmer = 0.5 + 0.5 * Math.sin(t * 2.2 + i * 0.62);
        const target = active
          ? Math.min(1, liveVal * 3 + 0.3 + 0.45 * shimmer * (0.4 + liveVal * 2))
          : 0.04 + 0.05 * shimmer;
        out[i] += (target - out[i]) * 0.22;
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [active, bins]);

  return dataRef;
}
