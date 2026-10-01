import type { ReactNode } from 'react';

// ---------------------------------------------------------------------------
// Shared Stark-HUD building blocks.
// ---------------------------------------------------------------------------

export function HudPanel({
  children,
  className = '',
  title,
  right,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  right?: ReactNode;
}) {
  return (
    <section className={`hud-panel p-4 ${className}`}>
      {title && (
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="hud-title">{title}</h2>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function HudTitle({ children }: { children: ReactNode }) {
  return <h2 className="hud-title">{children}</h2>;
}

type BtnVariant = 'default' | 'primary' | 'gold' | 'danger';

export function HudButton({
  children,
  onClick,
  disabled,
  variant = 'default',
  title,
  type = 'button',
  className = '',
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: BtnVariant;
  title?: string;
  type?: 'button' | 'submit';
  className?: string;
}) {
  const variantCls =
    variant === 'primary'
      ? 'hud-btn-primary'
      : variant === 'gold'
        ? 'hud-btn-gold'
        : variant === 'danger'
          ? 'hud-btn-danger'
          : '';
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`hud-btn ${variantCls} ${className}`}
    >
      {children}
    </button>
  );
}

export function HudInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`hud-input ${props.className ?? ''}`} />;
}

export function HudSelect(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`hud-input ${props.className ?? ''}`} />;
}

export function HudChip({
  children,
  onClick,
  disabled,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} className="hud-chip">
      {children}
    </button>
  );
}

export function HudToggle({
  on,
  onChange,
  label,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label ?? 'toggle'}
      data-on={on}
      className="hud-toggle"
      onClick={() => onChange(!on)}
    />
  );
}

/** Subtle hex-grid SVG background, pure CSS/SVG — no layout cost. */
export function HexGridBackground() {
  return (
    <svg
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 z-0 h-full w-full opacity-[0.10]"
    >
      <defs>
        <pattern id="hud-hex" width="56" height="97" patternUnits="userSpaceOnUse">
          <path
            d="M28 0 L56 16 L56 48 L28 64 L0 48 L0 16 Z M28 64 L56 80 L56 112 L28 128 L0 112 L0 80 Z"
            fill="none"
            stroke="#00f0ff"
            strokeWidth="1"
          />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="url(#hud-hex)" />
    </svg>
  );
}

/** Small telemetry gauge: label, big mono value, animated bar. */
export function Gauge({
  label,
  value,
  unit = '',
  percent,
  dangerAt = 90,
  warnAt = 70,
}: {
  label: string;
  value: string;
  unit?: string;
  percent: number;
  dangerAt?: number;
  warnAt?: number;
}) {
  const pct = Math.min(100, Math.max(0, percent));
  const color =
    pct >= dangerAt ? '#ef4444' : pct >= warnAt ? '#f59e0b' : '#00f0ff';
  return (
    <div className="hud-panel px-4 py-3">
      <div className="hud-subtitle mb-1">{label}</div>
      <div className="font-mono text-2xl font-bold" style={{ color }}>
        {value}
        {unit && <span className="ml-1 text-sm font-normal opacity-70">{unit}</span>}
      </div>
      <div className="hud-meter mt-2">
        <div style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${color}55, ${color})`, boxShadow: `0 0 10px ${color}` }} />
      </div>
    </div>
  );
}

export function HudError({ message }: { message: string }) {
  return (
    <p className="rounded-md border border-crimson/40 bg-crimson/10 px-3 py-2 font-mono text-xs text-red-300">
      ⚠ {message}
    </p>
  );
}

export function HudEmpty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-cyan-200/40">{children}</p>;
}
