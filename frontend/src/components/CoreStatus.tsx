import { useEffect, useState } from 'react';
import { getSystemStatus } from '../api';
import type { SystemStatusInfo } from '../api';
import { useMew } from '../mew/context';

// ---------------------------------------------------------------------------
// CoreStatus — developer-coded header readout. Polls GET /api/system/status
// every 30 s against the exact backend contract
//   { provider, model, online, memory, voice: {stt, tts}, rag, uptime_s }
// MODEL shows the real effective model (e.g. OLLAMA/QWEN3:0.6B or
// RULE_BASED/FALLBACK). VOICE comes from the LOCAL voice engine state —
// LISTENING appears only while the mic is genuinely active (never faked).
// If the backend is unreachable the readout shows OFFLINE honestly — no
// fake "online".
// ---------------------------------------------------------------------------

interface View {
  online: boolean | null; // null = not yet probed
  provider: string | null;
  model: string | null;
  modelDisplay: string | null;
  modelDegraded: boolean;
  memory: string | null;
  telegram: { configured: boolean; state: string } | null;
}

function Row({ label, value, ok }: { label: string; value: string; ok: boolean | null }) {
  return (
    <div>
      <span className="text-[#7de9ff]/60">{label} • </span>
      <span className={ok === true ? 'ok' : ok === false ? 'bad' : 'val'}>
        {value}
      </span>
    </div>
  );
}

export function CoreStatus() {
  const { voiceState } = useMew();
  const [view, setView] = useState<View>({
    online: null,
    provider: null,
    model: null,
    modelDisplay: null,
    modelDegraded: false,
    memory: null,
    telegram: null,
  });

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      let info: SystemStatusInfo | null = null;
      try {
        info = await getSystemStatus();
      } catch {
        info = null; // unreachable — honest OFFLINE
      }
      if (cancelled) return;
      if (info === null) {
        setView({
          online: false,
          provider: null,
          model: null,
          modelDisplay: null,
          modelDegraded: false,
          memory: null,
          telegram: null,
        });
      } else {
        setView({
          online: info.online,
          provider: info.provider || null,
          model: info.model || null,
          modelDisplay: info.model_display || null,
          modelDegraded: info.model_degraded === true,
          memory: info.memory || null,
          telegram: info.telegram || null,
        });
      }
    };
    void poll();
    const id = window.setInterval(() => void poll(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // Local voice engine state — real, not from the backend.
  const voiceLabel =
    voiceState === 'listening' || voiceState === 'recognizing'
      ? 'LISTENING'
      : voiceState === 'speaking'
        ? 'SPEAKING'
        : voiceState === 'error'
          ? 'ERROR'
          : 'IDLE';

  const coreLabel =
    view.online === null ? '…' : view.online ? 'ONLINE' : 'OFFLINE';
  // Prefer the backend-computed honest label (QWEN3:0.6B / FALLBACK
  // (RULE-BASED)); fall back to provider/model when the backend predates it.
  const modelLabel =
    view.online !== true
      ? '—'
      : (view.modelDisplay ||
          [view.provider, view.model].filter(Boolean).join('/').toUpperCase() ||
          'UNKNOWN');

  // Telegram: honest state from the backend. Only "CONNECTED" after a live
  // test; "NOT CONFIGURED" when env vars are missing; "—" when offline.
  const telegramLabel =
    view.online !== true
      ? '—'
      : view.telegram?.state === 'configured'
        ? 'READY'
        : 'NOT CONFIGURED';

  return (
    <div className="mew-core-status" role="status" aria-label="MEW core status">
      <Row
        label="MEW CORE"
        value={coreLabel}
        ok={view.online === null ? null : view.online}
      />
      <Row
        label="MODEL"
        value={modelLabel}
        ok={
          view.online === true
            ? view.modelDegraded
              ? null // degraded fallback: honest label, neutral dot
              : true
            : view.online === false
              ? false
              : null
        }
      />
      <Row
        label="MEMORY"
        value={
          view.online !== true ? '—' : (view.memory ?? 'UNKNOWN').toUpperCase()
        }
        ok={view.online === true ? null : view.online === false ? false : null}
      />
      <Row
        label="VOICE"
        value={voiceLabel}
        ok={null}
      />
      <Row
        label="TELEGRAM"
        value={telegramLabel}
        ok={view.online === true ? (view.telegram?.configured ? true : null) : null}
      />
    </div>
  );
}
