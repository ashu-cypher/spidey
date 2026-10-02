import { useEffect, useState } from 'react';
import { getSystemStatus } from '../api';
import type { SystemStatusInfo } from '../api';
import { useMew } from '../mew/context';

// ---------------------------------------------------------------------------
// CoreStatus — developer-coded header readout. Polls GET /api/system/status
// every 30 s against the exact backend contract
//   { provider, model, online, memory, voice, rag }
// VOICE comes from the LOCAL voice engine state (never faked). If the
// backend is unreachable the readout shows OFFLINE honestly — no fake
// "online".
// ---------------------------------------------------------------------------

interface View {
  online: boolean | null; // null = not yet probed
  provider: string | null;
  model: string | null;
  memory: string | null;
  voice: string | null;
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
    memory: null,
    voice: null,
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
        setView({ online: false, provider: null, model: null, memory: null, voice: null });
      } else {
        setView({
          online: info.online,
          provider: info.provider || null,
          model: info.model || null,
          memory: info.memory || null,
          voice: info.voice || null,
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
  const modelLabel =
    view.online !== true
      ? '—'
      : [view.provider, view.model].filter(Boolean).join('/').toUpperCase() ||
        'UNKNOWN';

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
        ok={view.online === true ? true : view.online === false ? false : null}
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
    </div>
  );
}
