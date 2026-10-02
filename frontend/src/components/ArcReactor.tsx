import { useEffect, useRef } from 'react';

// ---------------------------------------------------------------------------
// MEW spider core — Canvas 2D, devicePixelRatio-aware, single rAF loop.
// The character: a dark AI core with two glowing angled "eyes", red/blue
// rim lighting, web-line etchings on the casing, holographic scan rings,
// and an audio-reactive pulse fed by the existing spectrum analyser hook.
//
// Modes (driven by real app state via context):
//   idle       — breathing glow, slow drift
//   listening  — brightened eyes + spectrum waveform + spider-sense pulse
//   thinking   — rotating web elements, faster orbit nodes
//   searching  — nodes connecting along web lines, traveling highlight
//   analyzing  — horizontal scan sweep across the core
//   processing — thin blue sweep ring while a workflow TOOL step is RUNNING
//   speaking   — subtle pulse synced to TTS amplitude
//   success    — short web pulse when a run completes
//   error      — controlled red wash when a run fails
//   attention  — quick zoom flare on wake-word detection
//   spidersense— TRANSIENT (flashMode ~1.2s): eyes flare, radial pulse,
//                web lines illuminate; then the state machine moves on to
//                listening/thinking/analyzing. Triggered ONLY by real
//                events (recognition start, attach complete, tool_start).
//   alert      — protocol alerts (crimson, expanding wave)
// Reduced motion: one static glow frame, no loop. getContext() === null →
// nothing renders (no crash).
// ---------------------------------------------------------------------------

export type ReactorMode =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'searching'
  | 'analyzing'
  | 'processing'
  | 'speaking'
  | 'success'
  | 'error'
  | 'attention'
  | 'spidersense'
  | 'alert';

interface Props {
  mode: ReactorMode;
  spectrum: { current: number[] };
  size?: number;
}

const BLUE = '#38e1ff';
const BLUE_DIM = '#7de9ff';
const RED = '#e62429';
const WHITE = '#f4f7fb';
const NAVY = '#0a0f1e';

interface Particle {
  radius: number; // orbit radius as a multiple of R
  angle: number;
  speed: number;
  size: number;
  depth: number; // 0 = near/bright, 1 = far/dim
  voxel: boolean; // voxel squares (minecraft-subtle) vs soft motes
}

function makeParticles(): Particle[] {
  const ps: Particle[] = [];
  for (let i = 0; i < 30; i += 1) {
    const deep = i % 2 === 1;
    ps.push({
      radius: deep
        ? 1.02 + ((i * 37) % 48) / 100
        : 0.5 + ((i * 41) % 48) / 100,
      angle: (i / 30) * Math.PI * 2,
      speed: deep
        ? 0.2 + ((i * 53) % 60) / 130
        : 0.6 + ((i * 47) % 110) / 130,
      size: deep
        ? 1.2 + ((i * 29) % 14) / 10
        : 1.8 + ((i * 31) % 20) / 10,
      depth: deep ? 1 : 0,
      voxel: i % 3 === 0, // every third particle is a voxel square
    });
  }
  return ps;
}

const SPOKES = 12;
const NODES = 6;

export function ArcReactor({ mode, spectrum, size = 260 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const modeRef = useRef<ReactorMode>(mode);
  modeRef.current = mode;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx2d = canvas.getContext('2d');
    if (!ctx2d) return; // canvas unavailable — render nothing, no crash
    const ctx = ctx2d;

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    ctx.scale(dpr, dpr);

    const cx = size / 2;
    const cy = size / 2;
    const R = size * 0.36;

    // Dark core gradient: near-black center, deep navy edge.
    const coreGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.62);
    coreGrad.addColorStop(0, '#0d1526');
    coreGrad.addColorStop(0.55, '#0a0f1e');
    coreGrad.addColorStop(1, '#060609');

    // Ambient backdrop wash (blue, very faint).
    const backGlow = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.75);
    backGlow.addColorStop(0, 'rgba(56,225,255,0.10)');
    backGlow.addColorStop(0.55, 'rgba(56,225,255,0.04)');
    backGlow.addColorStop(1, 'rgba(56,225,255,0)');

    const particles = makeParticles();
    const nodes = new Array<number>(NODES);
    for (let i = 0; i < NODES; i += 1) nodes[i] = (i / NODES) * Math.PI * 2;

    const reduced =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let raf = 0;
    let last = performance.now();
    let webAngle = 0;
    let ringAngle = 0;
    let sweepAngle = 0;

    /** Draw one angled eye slit at (x, y), rotated by `rot`. */
    const drawEye = (
      x: number,
      y: number,
      rot: number,
      w: number,
      h: number,
      brightness: number,
      tint: string,
    ) => {
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(rot);
      // Angled slit: outer edge high, inner tip sweeping down.
      const g = ctx.createLinearGradient(0, -h, 0, h);
      g.addColorStop(0, WHITE);
      g.addColorStop(0.55, tint);
      g.addColorStop(1, BLUE);
      ctx.globalAlpha = Math.min(1, 0.35 + brightness);
      ctx.fillStyle = g;
      ctx.shadowColor = tint === RED ? RED : BLUE;
      ctx.shadowBlur = 18 + 26 * brightness;
      ctx.beginPath();
      ctx.moveTo(-w / 2, -h * 0.12);
      ctx.lineTo(w / 2, -h * 0.62);
      ctx.lineTo(w / 2 - w * 0.12, h * 0.42);
      ctx.lineTo(-w / 2 + w * 0.1, h * 0.08);
      ctx.closePath();
      ctx.fill();
      // Hot inner core of the eye.
      ctx.globalAlpha = Math.min(1, 0.5 + brightness);
      ctx.shadowBlur = 8;
      ctx.fillStyle = 'rgba(255,255,255,0.9)';
      ctx.beginPath();
      ctx.moveTo(-w * 0.28, -h * 0.18);
      ctx.lineTo(w * 0.28, -h * 0.42);
      ctx.lineTo(w * 0.24, h * 0.18);
      ctx.lineTo(-w * 0.24, -h * 0.02);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    };

    const draw = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const t = now / 1000;
      const m = modeRef.current;

      const idle = m === 'idle';
      const listening = m === 'listening';
      const thinking = m === 'thinking';
      const searching = m === 'searching';
      const analyzing = m === 'analyzing';
      const processing = m === 'processing';
      const speaking = m === 'speaking';
      const success = m === 'success';
      const error = m === 'error';
      const attention = m === 'attention';
      const spidersense = m === 'spidersense';
      const alert = m === 'alert';

      const active = listening || thinking || searching || analyzing || processing || speaking;

      // Spectrum drives the audio-reactive pulse in every mode.
      const spec = spectrum.current;
      let specAvg = 0;
      for (let i = 0; i < spec.length; i += 1) specAvg += spec[i];
      specAvg /= Math.max(1, spec.length);
      const audio = Math.min(1, specAvg);

      const breathe = 1 + 0.016 * Math.sin(t * 1.6);
      const speakPulse = speaking ? 1 + audio * 0.05 : 1;
      const sensePulse = spidersense ? 1 + 0.1 * Math.abs(Math.sin(t * 14)) : 1;
      const pulse = (listening ? 1 + audio * 0.1 : breathe) * speakPulse * sensePulse;
      const errorPulse = error ? 0.5 + 0.5 * Math.sin(t * 3.4) : 0;
      const successPulse = success ? 0.5 + 0.5 * Math.sin(t * 6) : 0;

      webAngle += dt * (thinking || spidersense ? 1.4 : searching ? 0.9 : 0.28);
      ringAngle += dt * (thinking ? 1.8 : 0.5);
      if (processing) sweepAngle += dt * 7.5;

      ctx.clearRect(0, 0, size, size);

      const rWeb = R * 1.62 * pulse;
      const rCasing = R * 1.14 * pulse;
      const rCore = R * 0.62 * pulse;

      // --- ambient backdrop wash ---
      ctx.save();
      ctx.globalAlpha = 0.7 + 0.5 * audio;
      ctx.fillStyle = backGlow;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 1.75, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      // --- faint web geometry behind everything (illuminates on spider-sense) ---
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(webAngle);
      ctx.strokeStyle = BLUE;
      const webAlpha = spidersense ? 0.5 : 0.075;
      ctx.globalAlpha = webAlpha;
      ctx.lineWidth = 1;
      for (let i = 0; i < SPOKES; i += 1) {
        const a = (i / SPOKES) * Math.PI * 2;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * R * 0.5, Math.sin(a) * R * 0.5);
        ctx.lineTo(Math.cos(a) * rWeb, Math.sin(a) * rWeb);
        ctx.stroke();
      }
      for (let k = 1; k <= 3; k += 1) {
        ctx.beginPath();
        ctx.arc(0, 0, (rWeb * k) / 3.4, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();

      // --- voxel-ish + mote particle drift in the backdrop ---
      {
        const energy = thinking ? 1 : searching ? 0.85 : 0.4;
        ctx.save();
        for (const p of particles) {
          p.angle += dt * p.speed * (thinking ? 1.7 : 0.55);
          const x = cx + Math.cos(p.angle) * R * p.radius * pulse;
          const y = cy + Math.sin(p.angle) * R * p.radius * pulse;
          const twinkle = 0.5 + 0.5 * Math.sin(t * 3 + p.angle * 4);
          ctx.globalAlpha = energy * twinkle * (p.depth === 1 ? 0.3 : 0.75);
          ctx.fillStyle = p.depth === 1 ? BLUE_DIM : BLUE;
          if (p.voxel) {
            // minecraft-subtle: tiny squares drifting
            const s = p.size * (thinking ? 1.4 : 1);
            ctx.fillRect(x - s / 2, y - s / 2, s, s);
          } else {
            ctx.shadowColor = BLUE;
            ctx.shadowBlur = p.depth === 1 ? 0 : 6;
            ctx.beginPath();
            ctx.arc(x, y, p.size * 0.6, 0, Math.PI * 2);
            ctx.fill();
          }
        }
        ctx.restore();
      }

      // --- orbit nodes along the web ring (small squares) ---
      {
        ctx.save();
        const nodeR = R * 1.3 * pulse;
        const pts: { x: number; y: number }[] = [];
        for (let i = 0; i < NODES; i += 1) {
          nodes[i] += dt * (searching ? 1.6 : thinking ? 1.1 : 0.4);
          const x = cx + Math.cos(nodes[i]) * nodeR;
          const y = cy + Math.sin(nodes[i]) * nodeR;
          pts.push({ x, y });
        }
        if (searching) {
          // nodes connecting along web lines: link each node to the next
          ctx.strokeStyle = BLUE;
          ctx.lineWidth = 1.2;
          for (let i = 0; i < NODES; i += 1) {
            const a = pts[i];
            const b = pts[(i + 1) % NODES];
            const travel = (t * 1.8 + i / NODES) % 1;
            ctx.globalAlpha = 0.18 + 0.5 * Math.abs(Math.sin(travel * Math.PI));
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
        for (const p of pts) {
          const s = searching ? 4.4 : 3.2;
          ctx.globalAlpha = searching ? 0.95 : 0.6;
          ctx.fillStyle = searching ? WHITE : BLUE;
          ctx.shadowColor = BLUE;
          ctx.shadowBlur = searching ? 10 : 5;
          ctx.fillRect(p.x - s / 2, p.y - s / 2, s, s);
        }
        ctx.restore();
      }

      // --- casing: dark navy ring with web-line etchings + red/blue rim ---
      ctx.save();
      ctx.strokeStyle = NAVY;
      ctx.lineWidth = R * 0.16;
      ctx.beginPath();
      ctx.arc(cx, cy, rCasing, 0, Math.PI * 2);
      ctx.stroke();
      // etchings: radial hairlines on the casing
      ctx.strokeStyle = BLUE;
      ctx.globalAlpha = spidersense ? 0.5 : 0.14;
      ctx.lineWidth = 1;
      for (let i = 0; i < 36; i += 1) {
        const a = (i / 36) * Math.PI * 2 + ringAngle * 0.3;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(a) * (rCasing - R * 0.07), cy + Math.sin(a) * (rCasing - R * 0.07));
        ctx.lineTo(cx + Math.cos(a) * (rCasing + R * 0.07), cy + Math.sin(a) * (rCasing + R * 0.07));
        ctx.stroke();
      }
      // rim lighting: red arc upper-left, blue arc lower-right
      ctx.globalAlpha = error || alert ? 0.9 : 0.75;
      ctx.lineWidth = 3;
      ctx.shadowBlur = 16;
      ctx.strokeStyle = RED;
      ctx.shadowColor = RED;
      ctx.beginPath();
      ctx.arc(cx, cy, rCasing + R * 0.09, Math.PI * 0.95, Math.PI * 1.65);
      ctx.stroke();
      ctx.strokeStyle = BLUE;
      ctx.shadowColor = BLUE;
      ctx.beginPath();
      ctx.arc(cx, cy, rCasing + R * 0.09, Math.PI * -0.05, Math.PI * 0.65);
      ctx.stroke();
      ctx.restore();

      // --- holographic scan rings (thin, dashed, counter-rotating) ---
      ctx.save();
      ctx.strokeStyle = BLUE;
      ctx.lineWidth = 1;
      ctx.setLineDash([6, 10]);
      ctx.globalAlpha = analyzing ? 0.8 : 0.35;
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(ringAngle);
      ctx.beginPath();
      ctx.arc(0, 0, rCore * 1.12, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(-ringAngle * 1.4);
      ctx.globalAlpha = 0.22;
      ctx.beginPath();
      ctx.arc(0, 0, rCore * 1.28, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
      ctx.setLineDash([]);
      ctx.restore();

      // --- dark core ---
      ctx.save();
      ctx.fillStyle = coreGrad;
      ctx.shadowColor = error ? RED : BLUE;
      ctx.shadowBlur = error ? 30 : 22 + 20 * audio;
      ctx.beginPath();
      ctx.arc(cx, cy, rCore, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      // --- the eyes: angled slits, brightness follows mode + audio ---
      {
        const eyeW = R * 0.52;
        const eyeH = R * 0.2;
        const eyeY = cy - R * 0.04;
        const eyeX = R * 0.31;
        let bright = 0.45 + 0.3 * audio;
        let tint = BLUE_DIM;
        if (spidersense) {
          bright = 1.6; // flare
          tint = WHITE;
        } else if (listening) {
          bright = 0.9 + 0.5 * audio;
        } else if (speaking) {
          bright = 0.75 + 0.4 * audio;
        } else if (thinking || searching || analyzing) {
          bright = 0.8;
        } else if (success) {
          bright = 1.0 + 0.4 * successPulse;
        } else if (error) {
          bright = 0.5;
          tint = RED;
        } else if (idle) {
          bright = 0.45 + 0.1 * Math.sin(t * 1.6);
        }
        drawEye(cx - eyeX, eyeY, -0.32, eyeW, eyeH, bright, tint);
        drawEye(cx + eyeX, eyeY, 0.32, eyeW, eyeH, bright, tint);
      }

      // --- spectrum waveform under the eyes (live when listening) ---
      const bins = spec.length;
      if (bins > 0 && (listening || speaking)) {
        ctx.save();
        ctx.strokeStyle = listening ? BLUE : BLUE_DIM;
        ctx.lineWidth = 2;
        ctx.globalAlpha = listening ? 0.85 : 0.5;
        const baseY = cy + R * 0.34;
        const span = R * 0.8;
        for (let i = 0; i < bins; i += 1) {
          const x = cx - span / 2 + (i / Math.max(1, bins - 1)) * span;
          const v = spec[i];
          const h = 2 + v * R * 0.28;
          ctx.beginPath();
          ctx.moveTo(x, baseY - h / 2);
          ctx.lineTo(x, baseY + h / 2);
          ctx.stroke();
        }
        ctx.restore();
      }

      // --- analyzing: horizontal scan sweep across the core ---
      if (analyzing) {
        const sy = cy + Math.sin(t * 3.2) * R * 0.5;
        ctx.save();
        ctx.globalAlpha = 0.5;
        ctx.fillStyle = BLUE;
        ctx.shadowColor = BLUE;
        ctx.shadowBlur = 12;
        ctx.fillRect(cx - rCore, sy - 1, rCore * 2, 2);
        ctx.globalAlpha = 0.12;
        ctx.fillRect(cx - rCore, sy - R * 0.08, rCore * 2, R * 0.16);
        ctx.restore();
      }

      // --- processing: thin fast blue sweep ring (workflow tools) ---
      if (processing) {
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(sweepAngle);
        ctx.strokeStyle = BLUE;
        ctx.globalAlpha = 0.9;
        ctx.lineWidth = 1.5;
        ctx.shadowColor = BLUE;
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(0, 0, rCore * 1.12, 0, Math.PI * 0.7);
        ctx.stroke();
        ctx.restore();
      }

      // --- spidersense: radial pulse + illuminated web ---
      if (spidersense) {
        const wave = (t * 2.4) % 1;
        ctx.save();
        ctx.globalAlpha = 0.65 * (1 - wave);
        ctx.strokeStyle = RED;
        ctx.lineWidth = 3;
        ctx.shadowColor = RED;
        ctx.shadowBlur = 18;
        ctx.beginPath();
        ctx.arc(cx, cy, rCasing * (1 + wave * 0.35), 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      // --- success: short web pulse ---
      if (success) {
        const wave = (t * 1.8) % 1;
        ctx.save();
        ctx.globalAlpha = 0.6 * (1 - wave);
        ctx.strokeStyle = BLUE;
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.arc(cx, cy, rCasing * (1 + wave * 0.28), 0, Math.PI * 2);
        ctx.stroke();
        // spokes on the pulse for the web feel
        ctx.globalAlpha *= 0.7;
        ctx.lineWidth = 1;
        for (let i = 0; i < 8; i += 1) {
          const a = (i / 8) * Math.PI * 2 + webAngle;
          const r = rCasing * (1 + wave * 0.28);
          ctx.beginPath();
          ctx.moveTo(cx + Math.cos(a) * (r - 8), cy + Math.sin(a) * (r - 8));
          ctx.lineTo(cx + Math.cos(a) * (r + 8), cy + Math.sin(a) * (r + 8));
          ctx.stroke();
        }
        ctx.restore();
      }

      // --- attention: quick zoom ring ---
      if (attention) {
        const wave = (t * 6) % 1;
        ctx.save();
        ctx.globalAlpha = 0.7 * (1 - wave);
        ctx.strokeStyle = WHITE;
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.arc(cx, cy, rCasing * (1 + wave * 0.22), 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      // --- error: controlled red wash (not a flood) ---
      if (error) {
        ctx.save();
        ctx.globalAlpha = 0.1 + 0.08 * errorPulse;
        ctx.fillStyle = RED;
        ctx.beginPath();
        ctx.arc(cx, cy, R * 1.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      // --- alert: crimson expanding ring ---
      if (alert) {
        const wave = (t * 1.4) % 1;
        ctx.save();
        ctx.globalAlpha = 0.5 * (1 - wave);
        ctx.strokeStyle = RED;
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(cx, cy, rCasing * (1 + wave * 0.35), 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      // silence unused-var warnings for the explicit branch flags
      void active;
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
      aria-label={`MEW spider core — ${mode}`}
    />
  );
}
