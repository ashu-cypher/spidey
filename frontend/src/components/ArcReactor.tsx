import { useEffect, useRef } from 'react';

// ---------------------------------------------------------------------------
// Arc Reactor — Stark-HUD centerpiece. Canvas 2D, devicePixelRatio-aware,
// single rAF loop. All animation is transform/opacity-level canvas drawing
// (no DOM layout work). Reads spectrum imperatively via ref: no re-renders.
// ---------------------------------------------------------------------------

export type ReactorMode = 'idle' | 'listening' | 'thinking' | 'speaking' | 'alert';

interface Props {
  mode: ReactorMode;
  spectrum: { current: number[] };
  size?: number;
}

const CYAN = '#00f0ff';
const GOLD = '#f59e0b';
const CRIMSON = '#ef4444';

export function ArcReactor({ mode, spectrum, size = 260 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const modeRef = useRef<ReactorMode>(mode);
  modeRef.current = mode;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx2d = canvas.getContext('2d');
    if (!ctx2d) return;
    const ctx = ctx2d;

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    ctx.scale(dpr, dpr);

    const cx = size / 2;
    const cy = size / 2;
    const R = size * 0.36;

    // Cached core radial gradient (rebuilt on resize only).
    const coreGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.5);
    coreGrad.addColorStop(0, 'rgba(255,255,255,0.95)');
    coreGrad.addColorStop(0.35, 'rgba(160,250,255,0.85)');
    coreGrad.addColorStop(1, 'rgba(0,240,255,0.05)');

    // Precomputed tick angles for the outer ring.
    const TICKS = 72;
    const tickAngles = new Array<number>(TICKS);
    for (let i = 0; i < TICKS; i += 1) tickAngles[i] = (i / TICKS) * Math.PI * 2;

    let raf = 0;
    let last = performance.now();
    let angle = 0;
    let arcAngle = 0;

    const loop = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const t = now / 1000;
      const m = modeRef.current;

      const alert = m === 'alert';
      const thinking = m === 'thinking';
      const speaking = m === 'speaking';
      const listening = m === 'listening';

      const ringColor = alert ? CRIMSON : CYAN;

      // Spectrum average drives the listening pulse.
      const spec = spectrum.current;
      let specAvg = 0;
      for (let i = 0; i < spec.length; i += 1) specAvg += spec[i];
      specAvg /= Math.max(1, spec.length);

      const breathe = 1 + 0.018 * Math.sin(t * 1.6);
      const pulse = listening ? 1 + specAvg * 0.14 : breathe;
      const alertPulse = alert ? 0.5 + 0.5 * Math.sin(t * 9) : 0;

      angle += dt * (thinking ? 2.6 : 0.45);
      arcAngle -= dt * (thinking ? 4.2 : 0.7);

      ctx.clearRect(0, 0, size, size);

      const rOuter = R * 1.22 * pulse;
      const rMid = R * pulse;
      const rCore = R * 0.46 * pulse;

      // --- outer tick ring (slow rotation) ---
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(angle);
      ctx.strokeStyle = ringColor;
      ctx.globalAlpha = alert ? 0.55 + 0.45 * alertPulse : 0.75;
      ctx.lineWidth = 1.5;
      for (let i = 0; i < TICKS; i += 1) {
        const a = tickAngles[i];
        const long = i % 6 === 0;
        const r1 = rOuter + (long ? 2 : 6);
        const r2 = rOuter + (long ? 12 : 10);
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
        ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
        ctx.stroke();
      }
      ctx.restore();

      // --- segmented arcs (counter-rotating UI ring) ---
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(arcAngle);
      ctx.strokeStyle = ringColor;
      ctx.globalAlpha = 0.85;
      ctx.lineWidth = 3;
      for (let k = 0; k < 3; k += 1) {
        const start = (k / 3) * Math.PI * 2;
        ctx.beginPath();
        ctx.arc(0, 0, rMid * 1.06, start, start + Math.PI * 0.42);
        ctx.stroke();
      }
      ctx.restore();

      // --- middle glow ring (single shadowBlur use per frame) ---
      ctx.save();
      ctx.strokeStyle = ringColor;
      ctx.globalAlpha = alert ? 0.5 + 0.5 * alertPulse : 0.6 + 0.15 * Math.sin(t * 2.4);
      ctx.lineWidth = 2.5;
      ctx.shadowColor = ringColor;
      ctx.shadowBlur = 18;
      ctx.beginPath();
      ctx.arc(cx, cy, rMid, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();

      // --- spectrum bars around the core (live when listening) ---
      const bins = spec.length;
      if (bins > 0) {
        ctx.save();
        ctx.strokeStyle = ringColor;
        ctx.lineWidth = 2;
        ctx.globalAlpha = listening ? 0.9 : 0.22;
        for (let i = 0; i < bins; i += 1) {
          const a = (i / bins) * Math.PI * 2 - Math.PI / 2;
          const v = spec[i];
          const r1 = rCore * 1.25;
          const r2 = r1 + v * R * 0.3;
          ctx.beginPath();
          ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
          ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
          ctx.stroke();
        }
        ctx.restore();
      }

      // --- core ---
      ctx.save();
      ctx.globalAlpha = alert ? 0.75 + 0.25 * alertPulse : 0.9;
      ctx.fillStyle = coreGrad;
      ctx.beginPath();
      ctx.arc(cx, cy, rCore, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      // --- speaking: gold flare overlay, alpha oscillates ---
      if (speaking) {
        ctx.save();
        ctx.globalAlpha = 0.22 + 0.2 * Math.abs(Math.sin(t * 7));
        ctx.strokeStyle = GOLD;
        ctx.lineWidth = 6;
        ctx.shadowColor = GOLD;
        ctx.shadowBlur = 24;
        ctx.beginPath();
        ctx.arc(cx, cy, rMid * 0.88, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      // --- alert: crimson expanding ring ---
      if (alert) {
        const wave = (t * 1.4) % 1;
        ctx.save();
        ctx.globalAlpha = 0.5 * (1 - wave);
        ctx.strokeStyle = CRIMSON;
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(cx, cy, rOuter * (1 + wave * 0.35), 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      raf = requestAnimationFrame(loop);
    };

    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
    // spectrum is a stable ref object; mode flows through modeRef.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Arc reactor — ${mode}`}
    />
  );
}
