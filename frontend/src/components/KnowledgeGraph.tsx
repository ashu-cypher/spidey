import { useEffect, useMemo, useState } from 'react';

// Lightweight SVG knowledge-graph widget: deterministic radial layout,
// no simulation libraries, no new npm dependencies.

interface Entity {
  id: string;
  type: string;
  name: string;
}

interface Relation {
  from: string;
  to: string;
  relation: string;
}

const TYPE_COLORS: Record<string, string> = {
  project: '#38e1ff',
  repository: '#38e1ff',
  technology: '#e62429',
  skill: '#67e8f9',
  document: '#a78bfa',
  person: '#fbbf24',
  goal: '#4ade80',
  task: '#f472b6',
  interest: '#fb923c',
  course: '#94a3b8',
  assignment: '#94a3b8',
};

function colorFor(type: string): string {
  return TYPE_COLORS[type.toLowerCase()] ?? '#94a3b8';
}

function label(text: string, max = 14): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

interface NodePos extends Entity {
  x: number;
  y: number;
  ring: 'you' | 'inner' | 'outer';
}

const W = 820;
const H = 560;
const CX = W / 2;
const CY = H / 2;

export function KnowledgeGraph() {
  const [entities, setEntities] = useState<Entity[]>([]);
  const [relations, setRelations] = useState<Relation[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetch('/api/knowledge/graph')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (!live) return;
        setEntities(Array.isArray(data.entities) ? data.entities : []);
        setRelations(Array.isArray(data.relations) ? data.relations : []);
        setLoading(false);
      })
      .catch(() => {
        if (!live) return;
        setFailed(true);
        setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);

  const nodes: NodePos[] = useMemo(() => {
    const you: NodePos = {
      id: 'you',
      type: 'you',
      name: 'YOU',
      x: CX,
      y: CY,
      ring: 'you',
    };
    const projects = entities.filter((e) =>
      ['project', 'repository'].includes(e.type.toLowerCase()),
    );
    const others = entities.filter(
      (e) => !['project', 'repository'].includes(e.type.toLowerCase()),
    );
    const byName = new Map(entities.map((e) => [e.name, e]));
    const projToOuter = new Map<string, number>();
    others.forEach((e, i) => {
      // Prefer the project this entity links to, else spread by index.
      const linked = relations.find(
        (r) =>
          (r.from === e.name && byName.get(r.to)?.type.toLowerCase() === 'project') ||
          (r.to === e.name && byName.get(r.from)?.type.toLowerCase() === 'project'),
      );
      const anchor = linked
        ? (r_name(linked, e.name))
        : null;
      const pi = anchor
        ? projects.findIndex((p) => p.name === anchor)
        : -1;
      projToOuter.set(e.name, pi >= 0 ? pi : i % Math.max(projects.length, 1));
    });
    const inner: NodePos[] = projects.map((e, i) => {
      const a = (2 * Math.PI * i) / Math.max(projects.length, 1) - Math.PI / 2;
      return { ...e, x: CX + 170 * Math.cos(a), y: CY + 130 * Math.sin(a), ring: 'inner' };
    });
    const outer: NodePos[] = others.map((e, oi) => {
      const anchorIdx = projToOuter.get(e.name) ?? 0;
      const base = projects.length
        ? (2 * Math.PI * anchorIdx) / projects.length - Math.PI / 2
        : 0;
      const spread = projects.length ? 2.4 / Math.max(1, projects.length) : 0.6;
      const a = base + spread * (oi - others.length / 2) * 0.35;
      return { ...e, x: CX + 330 * Math.cos(a), y: CY + 250 * Math.sin(a), ring: 'outer' };
    });
    return [you, ...inner, ...outer];
  }, [entities, relations]);

  const nodeByName = useMemo(() => new Map(nodes.map((n) => [n.name, n])), [nodes]);

  const selectedRelations = useMemo(() => {
    if (!selected) return [];
    return relations.filter((r) => r.from === selected || r.to === selected);
  }, [relations, selected]);

  const connectedNames = useMemo(() => {
    if (!selected) return new Set<string>();
    const s = new Set<string>([selected]);
    selectedRelations.forEach((r) => {
      s.add(r.from);
      s.add(r.to);
    });
    return s;
  }, [selected, selectedRelations]);

  if (loading) {
    return (
      <div className="rounded-xl border border-accent/25 bg-carbon/60 p-8 text-center font-mono text-sm text-cyan-200/60">
        Reading your knowledge web…
      </div>
    );
  }

  if (failed) {
    return (
      <div className="rounded-xl border border-accent/25 bg-carbon/60 p-8 text-center font-mono text-sm text-cyan-200/60">
        The knowledge graph is unreachable — is the MEW backend running?
      </div>
    );
  }

  if (entities.length === 0) {
    return (
      <div className="rounded-xl border border-accent/25 bg-carbon/60 p-10 text-center">
        <div className="mb-3 text-4xl" aria-hidden="true">
          🕸️
        </div>
        <p className="font-mono text-sm text-cyan-200/80">
          Your knowledge graph is empty — tell MEW about your projects
        </p>
        <p className="mt-2 font-mono text-xs text-cyan-200/50">
          e.g. “My main project is MEW, it uses Ollama and RAG”
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-accent/25 bg-carbon/60 p-4">
      <div className="flex flex-col gap-4 md:flex-row">
        <div className="min-w-0 flex-1">
          <svg
            viewBox={`0 0 ${W} ${H}`}
            className="h-auto w-full"
            role="img"
            aria-label="Knowledge graph"
          >
            <defs>
              <filter id="kg-glow" x="-60%" y="-60%" width="220%" height="220%">
                <feGaussianBlur stdDeviation="4" result="b" />
                <feMerge>
                  <feMergeNode in="b" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>

            {/* edges */}
            {relations.map((r, i) => {
              const a = nodeByName.get(r.from);
              const b = nodeByName.get(r.to);
              if (!a || !b) return null;
              const active = selected && (r.from === selected || r.to === selected);
              const dim = selected && !active;
              return (
                <line
                  key={i}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke="#38e1ff"
                  strokeOpacity={active ? 0.85 : dim ? 0.05 : 0.22}
                  strokeWidth={active ? 2 : 1}
                />
              );
            })}

            {/* nodes */}
            {nodes.map((n) => {
              const isSel = selected === n.name;
              const dim = selected && !connectedNames.has(n.name);
              const r = n.ring === 'you' ? 34 : n.ring === 'inner' ? 26 : 19;
              const c = n.ring === 'you' ? '#e62429' : colorFor(n.type);
              return (
                <g
                  key={n.id}
                  onClick={() => setSelected(isSel ? null : n.name)}
                  className="cursor-pointer"
                  opacity={dim ? 0.25 : 1}
                >
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r={r}
                    fill="#060609"
                    fillOpacity={0.85}
                    stroke={c}
                    strokeWidth={isSel ? 3 : 1.5}
                    strokeOpacity={isSel ? 1 : 0.75}
                    filter="url(#kg-glow)"
                  />
                  <text
                    x={n.x}
                    y={n.y + 4}
                    textAnchor="middle"
                    fontSize={n.ring === 'you' ? 13 : 10}
                    fontFamily="monospace"
                    fontWeight="bold"
                    fill={c}
                  >
                    {label(n.name, n.ring === 'you' ? 8 : 12)}
                  </text>
                  {n.ring !== 'you' && (
                    <text
                      x={n.x}
                      y={n.y + r + 13}
                      textAnchor="middle"
                      fontSize={9}
                      fontFamily="monospace"
                      fill="#a5f3fc"
                      fillOpacity={0.55}
                    >
                      {label(n.type)}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
          <p className="mt-2 text-center font-mono text-[11px] text-cyan-200/40">
            Click a node to see its connections
          </p>
        </div>

        {/* side panel */}
        <aside className="w-full shrink-0 rounded-lg border border-accent/20 bg-black/40 p-4 md:w-72">
          {selected ? (
            <>
              <h3 className="font-mono text-sm font-bold uppercase tracking-widest text-white">
                {selected}
              </h3>
              <p className="mt-1 font-mono text-[11px] text-cyan-200/50">
                {nodeByName.get(selected)?.type ?? ''} · {selectedRelations.length}{' '}
                connection{selectedRelations.length === 1 ? '' : 's'}
              </p>
              <ul className="mt-3 space-y-1.5">
                {selectedRelations.map((r, i) => (
                  <li key={i} className="font-mono text-xs text-cyan-200/80">
                    {r.from === selected ? (
                      <>
                        <span className="text-accent">{r.relation}</span> → {r.to}
                      </>
                    ) : (
                      <>
                        {r.from} <span className="text-accent">→{r.relation}→</span>
                      </>
                    )}
                  </li>
                ))}
              </ul>
              {selectedRelations.length === 0 && (
                <p className="mt-3 font-mono text-xs text-cyan-200/50">
                  No links yet — tell MEW how it connects to other things.
                </p>
              )}
              <button
                type="button"
                onClick={() => setSelected(null)}
                className="mt-4 font-mono text-[11px] text-accent/80 hover:text-accent"
              >
                ✕ clear selection
              </button>
            </>
          ) : (
            <>
              <h3 className="font-mono text-sm font-bold uppercase tracking-widest text-white">
                Your web
              </h3>
              <p className="mt-2 font-mono text-xs text-cyan-200/60">
                {entities.length} entities · {relations.length} links — everything
                MEW has learned about you, your projects, and your tools.
              </p>
              <p className="mt-4 font-mono text-[11px] text-cyan-200/40">
                Tip: ask MEW “what do you remember about{' '}
                {entities[0]?.name ?? 'X'}?”
              </p>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

// Name of the project an outer entity is anchored to, for ring placement.
function r_name(rel: Relation, me: string): string | null {
  if (rel.from === me) return rel.to;
  if (rel.to === me) return rel.from;
  return null;
}
