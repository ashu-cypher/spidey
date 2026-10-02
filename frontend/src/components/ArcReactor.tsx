import { useEffect, useRef } from 'react';

// ---------------------------------------------------------------------------
// Arc Reactor — Stark-HUD centerpiece. Canvas 2D, devicePixelRatio-aware,
// single rAF loop. All animation is transform/opacity-level canvas drawing
// (no DOM layout work). Reads spectrum imperatively via ref: no re-renders.
//
// Modes (driven by real app state via context):
//   idle       — breathing glow
//   listening  — brightened + spectrum pulse
//   thinking   — rotation + drifting data particles
//   speaking   — gold flare (synced to TTS state)
//   processing — thin cyan sweep ring while a workflow TOOL step is RUNNING
//   success    — brief gold pulse when a run completes
//   error      — crimson pulse when a run fails
//   attention  — quick zoom flare on wake-word detection
//   alert      — protocol alerts (crimson, expanding wave)
// Reduced motion: one static glow frame, no loop. getContext() === null →
// nothing renders (no crash).
// ---------------------------------------------------------------------------

export type ReactorMode =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'processing'
  | 'success'
  | 'error'
  | 'attention'
  | 'alert';

interface Props {
  mode: ReactorMode;
  spectrum: { current: number[] };
  size?: number;
}

const CYAN = '#00f0ff';
const GOLD = '#f59e0b';
const CRIMSON = '#ef4444';

interface Particle {
  radius: number; // orbit radius as a multiple of R
  angle: number;
  speed: number;
  size: number;
  depth: number; // 0 = near/bright, 1 = far/dim — particle depth layering
}

function makeParticles(): Particle[] {
  const ps: Particle[] = [];
  for (let i = 0; i < 28; i += 1) {
    const deep = i % 2 === 1;
    ps.push({
      radius: deep
        ? 1.05 + ((i * 37) % 45) / 100 // far layer: 1.05–1.49 R
        : 0.55 + ((i * 41) % 45) / 100, // near layer: 0.55–0.99 R
      angle: (i / 28) * Math.PI * 2,
      speed: deep
        ? 0.25 + ((i * 53) % 60) / 120 // far drifts slow: 0.25–0.75 rad/s
        : 0.7 + ((i * 47) % 110) / 120, // near moves fast: 0.7–1.6 rad/s
      size: deep
        ? 0.8 + ((i * 29) % 12) / 12 // far: 0.8–1.7 px
        : 1.6 + ((i * 31) % 24) / 12, // near: 1.6–3.5 px
      depth: deep ? 1 : 0,
    });
  }
  return ps;
}

export function ArcReactor({ mode, spectrum, size = 260 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const modeRef = useRef<ReactorMode>(mode);
  modeRef.current = mode;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx2d = canvas.getContext('2d');
    if (!ctx2d) return; // canvas/WebGL unavailable — render nothing, no crash
    const ctx = ctx2d;

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    ctx.scale(dpr, dpr);

    const cx = size / 2;
    const cy = size / 2;
    const R = size * 0.36;

    // Cached core radial gradient (rebuilt on resize only) — hot white
    // center, cyan mid-band, faint gold halo at the rim.
    const coreGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.5);
    coreGrad.addColorStop(0, 'rgba(255,255,255,1)');
    coreGrad.addColorStop(0.22, 'rgba(220,255,255,0.95)');
    coreGrad.addColorStop(0.5, 'rgba(120,235,255,0.75)');
    coreGrad.addColorStop(0.78, 'rgba(0,240,255,0.35)');
    coreGrad.addColorStop(0.92, 'rgba(245,158,11,0.12)');
    coreGrad.addColorStop(1, 'rgba(0,240,255,0.02)');

    // Ambient outer glow wash behind the whole reactor.
    const glowGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.7);
    glowGrad.addColorStop(0, 'rgba(0,240,255,0.16)');
    glowGrad.addColorStop(0.55, 'rgba(0,240,255,0.06)');
    glowGrad.addColorStop(1, 'rgba(0,240,255,0)');
    const alertGlowGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.7);
    alertGlowGrad.addColorStop(0, 'rgba(239,68,68,0.20)');
    alertGlowGrad.addColorStop(0.55, 'rgba(239,68,68,0.08)');
    alertGlowGrad.addColorStop(1, 'rgba(239,68,68,0)');

    // Precomputed tick angles for the outer ring.
    const TICKS = 72;
    const tickAngles = new Array<number>(TICKS);
    for (let i = 0; i < TICKS; i += 1) tickAngles[i] = (i / TICKS) * Math.PI * 2;

    const particles = makeParticles();
    const reduced =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let raf = 0;
    let last = performance.now();
    let angle = 0;
    let arcAngle = 0;
    let sweepAngle = 0;

    const draw = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const t = now / 1000;
      const m = modeRef.current;

      const alert = m === 'alert';
      const thinking = m === 'thinking';
      const speaking = m === 'speaking';
      const listening = m === 'listening';
      const processing = m === 'processing';
      const success = m === 'success';
      const error = m === 'error';
      const attention = m === 'attention';

      const ringColor = alert || error ? CRIMSON : CYAN;

      // Spectrum average drives audio-reactive effects in every mode:
      // listening pulses, the core breathes with the live level.
      const spec = spectrum.current;
      let specAvg = 0;
      for (let i = 0; i < spec.length; i += 1) specAvg += spec[i];
      specAvg /= Math.max(1, spec.length);
      const audioBoost = 1 + Math.min(1, specAvg) * 0.12;

      const breathe = 1 + 0.018 * Math.sin(t * 1.6);
      const pulse = listening
        ? 1 + specAvg * 0.14
        : attention
          ? 1 + 0.07 * Math.abs(Math.sin(t * 11)) // quick zoom flare
          : breathe;
      const alertPulse = alert ? 0.5 + 0.5 * Math.sin(t * 9) : 0;
      const errorPulse = error ? 0.5 + 0.5 * Math.sin(t * 3.2) : 0;
      const successPulse = success ? 0.5 + 0.5 * Math.sin(t * 6) : 0;

      angle += dt * (thinking ? 2.6 : 0.45);
      arcAngle -= dt * (thinking ? 4.2 : 0.7);
      sweepAngle += dt * (processing ? 7.5 : 0); // fast cyan sweep

      ctx.clearRect(0, 0, size, size);

      const rOuter = R * 1.22 * pulse;
      const rMid = R * pulse;
      const rCore = R * 0.46 * pulse * audioBoost;

      // --- ambient glow wash (audio-reactive) ---
      ctx.save();
      ctx.globalAlpha =
        (alert || error ? 0.55 + 0.45 * (alert ? alertPulse : errorPulse) : 0.8) *
        (0.75 + 0.5 * Math.min(1, specAvg));
      ctx.fillStyle = alert || error ? alertGlowGrad : glowGrad;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 1.7, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      // --- outer tick ring (slow rotation) ---
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(angle);
      ctx.strokeStyle = ringColor;
      ctx.globalAlpha =
        alert || error ? 0.55 + 0.45 * (alert ? alertPulse : errorPulse) : 0.75;
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

      // --- processing: thin fast cyan sweep ring (distinct from the UI ring) ---
      if (processing) {
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(sweepAngle);
        ctx.strokeStyle = CYAN;
        ctx.globalAlpha = 0.9;
        ctx.lineWidth = 1;
        ctx.shadowColor = CYAN;
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(0, 0, rMid * 1.16, 0, Math.PI * 0.7);
        ctx.stroke();
        ctx.restore();
        // trailing echo arc
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(sweepAngle - 0.9);
        ctx.strokeStyle = CYAN;
        ctx.globalAlpha = 0.35;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(0, 0, rMid * 1.16, 0, Math.PI * 0.35);
        ctx.stroke();
        ctx.restore();
      }

      // --- data particles: two depth layers, always drifting; brighter
      // and faster while thinking ---
      {
        const energy = thinking ? 1 : 0.35;
        ctx.save();
        for (const p of particles) {
          p.angle += dt * p.speed * (thinking ? 1.6 : 0.5);
          const x = cx + Math.cos(p.angle) * R * p.radius * pulse;
          const y = cy + Math.sin(p.angle) * R * p.radius * pulse;
          const twinkle = 0.5 + 0.5 * Math.sin(t * 3 + p.angle * 4);
          ctx.globalAlpha =
            energy * twinkle * (p.depth === 1 ? 0.35 : 0.8);
          ctx.fillStyle = p.depth === 1 ? '#7dd3fc' : CYAN;
          ctx.shadowColor = CYAN;
          ctx.shadowBlur = p.depth === 1 ? 0 : 6;
          ctx.beginPath();
          ctx.arc(x, y, p.size, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.restore();
      }

      // --- middle glow ring (stronger glow) ---
      ctx.save();
      ctx.strokeStyle = ringColor;
      ctx.globalAlpha =
        alert || error
          ? 0.5 + 0.5 * (alert ? alertPulse : errorPulse)
          : 0.65 + 0.2 * Math.sin(t * 2.4) + 0.15 * Math.min(1, specAvg);
      ctx.lineWidth = 2.5;
      ctx.shadowColor = ringColor;
      ctx.shadowBlur = 28;
      ctx.beginPath();
      ctx.arc(cx, cy, rMid, 0, Math.PI * 2);
      ctx.stroke();
      // halo echo ring for extra glow depth
      ctx.globalAlpha *= 0.35;
      ctx.lineWidth = 7;
      ctx.shadowBlur = 34;
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

      // --- core (audio-reactive size + glow) ---
      ctx.save();
      ctx.globalAlpha =
        alert || error
          ? 0.75 + 0.25 * (alert ? alertPulse : errorPulse)
          : success
            ? 0.85 + 0.15 * successPulse
            : 0.9;
      ctx.fillStyle = coreGrad;
      ctx.shadowColor = alert || error ? CRIMSON : CYAN;
      ctx.shadowBlur = 30 + 26 * Math.min(1, specAvg);
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

      // --- success: brief gold pulse ring ---
      if (success) {
        ctx.save();
        ctx.globalAlpha = 0.35 + 0.55 * successPulse;
        ctx.strokeStyle = GOLD;
        ctx.lineWidth = 4;
        ctx.shadowColor = GOLD;
        ctx.shadowBlur = 22;
        ctx.beginPath();
        ctx.arc(cx, cy, rMid * 1.1 * (1 + 0.05 * successPulse), 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      // --- attention: quick cyan/white zoom ring ---
      if (attention) {
        const wave = (t * 6) % 1;
        ctx.save();
        ctx.globalAlpha = 0.7 * (1 - wave);
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.arc(cx, cy, rMid * (1 + wave * 0.22), 0, Math.PI * 2);
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
    };

    if (reduced) {
      // Reduced motion: a single static glow frame, no loop.
      last = performance.now();
      draw(last);
      return undefined;
    }

    const loop = (now: number) => {
      draw(now);
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
