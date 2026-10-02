// ---------------------------------------------------------------------------
// MewEmblem — original geometric spider mark for MEW. Hand-built SVG:
// an angular spider silhouette (elongated hexagon abdomen, diamond
// cephalothorax, 8 angular legs) over faint concentric web arcs. Deliberately
// geometric/abstract — not a copy of any existing character mark. Legible
// down to 16px (thick strokes, no fine detail).
//
// Props: size (px), primary color, accent color, optional futuristic
// wordmark rendered next to the mark.
// ---------------------------------------------------------------------------

interface Props {
  size?: number;
  /** Spider body/legs color. */
  color?: string;
  /** Web-arc color. */
  accent?: string;
  /** Render the futuristic "MEW" lettering beside the mark. */
  wordmark?: boolean;
  className?: string;
  title?: string;
}

export const MEW_RED = '#e62429';
export const MEW_BLUE = '#38e1ff';

export function MewEmblem({
  size = 32,
  color = MEW_RED,
  accent = MEW_BLUE,
  wordmark = false,
  className,
  title = 'MEW emblem',
}: Props) {
  const w = wordmark ? 148 : 64;
  return (
    <svg
      width={size * (w / 64)}
      height={size}
      viewBox={`0 0 ${w} 64`}
      fill="none"
      role="img"
      aria-label={title}
      className={className}
    >
      {/* faint web geometry behind the mark */}
      <g opacity="0.22" stroke={accent} strokeWidth="1">
        <circle cx="32" cy="34" r="27" />
        <circle cx="32" cy="34" r="20" opacity="0.7" />
        <path d="M32 7 V13 M32 55 V61 M5 34 H11 M53 34 H59" opacity="0.8" />
      </g>
      {/* legs: 4 angular pairs, symmetric */}
      <g
        stroke={color}
        strokeWidth="3.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M29 35 L17 27 L9 29" />
        <path d="M28 39 L15 37 L7 41" />
        <path d="M28 43 L15 47 L8 53" />
        <path d="M29 47 L19 55 L15 61" />
        <path d="M35 35 L47 27 L55 29" />
        <path d="M36 39 L49 37 L57 41" />
        <path d="M36 43 L49 47 L56 53" />
        <path d="M35 47 L45 55 L49 61" />
      </g>
      {/* abdomen: elongated hexagon */}
      <polygon
        points="32,38 38,43 38,54 32,59 26,54 26,43"
        fill={color}
      />
      {/* abdomen chevron detail */}
      <path
        d="M32 44 L35.5 47 M32 48 L35.5 51 M32 44 L28.5 47 M32 48 L28.5 51"
        stroke="#060609"
        strokeWidth="1.4"
        strokeLinecap="round"
        opacity="0.85"
      />
      {/* cephalothorax: diamond */}
      <polygon points="32,28 36.5,32 32,37 27.5,32" fill={color} />
      {/* eyes: two angled slits */}
      <path
        d="M29.4 31.6 L31 30.4 M34.6 31.6 L33 30.4"
        stroke={accent}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      {wordmark && (
        <text
          x="68"
          y="41"
          fontFamily="ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
          fontSize="24"
          fontWeight="700"
          letterSpacing="6"
          fill="#f4f7fb"
        >
          MEW
        </text>
      )}
    </svg>
  );
}
