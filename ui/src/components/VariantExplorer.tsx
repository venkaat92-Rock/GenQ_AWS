import { useEffect, useMemo, useRef, useState } from 'react'
import ProcessMap, { END, MapEdge, MapNode, START } from './ProcessMap'

export interface Variant {
  variant: string
  activities: string[]
  case_count: number
  share: number
}
export interface DfgEdge {
  source: string
  target: string
  count: number
  mean_seconds: number
}

// Build the displayed graph from the ticked variants only. Frequencies are exact
// (derived from the variant traces); edge durations come from the full log's
// performance DFG and are labeled as overall averages.
function buildGraph(selected: Variant[], durations: Map<string, number>) {
  const events = new Map<string, number>()
  const counts = new Map<string, number>()
  const bump = (src: string, tgt: string, n: number) =>
    counts.set(`${src}→${tgt}`, (counts.get(`${src}→${tgt}`) ?? 0) + n)

  for (const v of selected) {
    const acts = v.activities
    if (acts.length === 0) continue
    bump(START, acts[0], v.case_count)
    acts.forEach((a, i) => {
      events.set(a, (events.get(a) ?? 0) + v.case_count)
      if (i > 0) bump(acts[i - 1], a, v.case_count)
    })
    bump(acts[acts.length - 1], END, v.case_count)
  }

  const nodes: MapNode[] = [...events.entries()].map(([activity, n]) => ({ activity, events: n }))
  const edges: MapEdge[] = [...counts.entries()].map(([key, count]) => {
    const [source, target] = key.split('→')
    return { source, target, count, meanSeconds: durations.get(key) }
  })
  return { nodes, edges }
}

export default function VariantExplorer(props: { variants: Variant[]; dfgEdges: DfgEdge[] }) {
  const { variants, dfgEdges } = props
  const [ticked, setTicked] = useState<Set<number>>(new Set(variants.map((_, i) => i)))
  const [mode, setMode] = useState<'freq' | 'perf'>('freq')
  const [zoom, setZoom] = useState(1)
  const panRef = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null)

  // variants can change when global filters change — keep selection valid
  useEffect(() => {
    setTicked(new Set(variants.map((_, i) => i)))
  }, [variants])

  const durations = useMemo(
    () => new Map(dfgEdges.map((e) => [`${e.source}→${e.target}`, e.mean_seconds])),
    [dfgEdges],
  )
  const selected = variants.filter((_, i) => ticked.has(i))
  const { nodes, edges } = useMemo(() => buildGraph(selected, durations), [variants, ticked, durations])

  const totalCases = variants.reduce((s, v) => s + v.case_count, 0)
  const coveredCases = selected.reduce((s, v) => s + v.case_count, 0)

  const toggle = (i: number) => {
    const next = new Set(ticked)
    if (next.has(i)) next.delete(i)
    else next.add(i)
    setTicked(next)
  }

  return (
    <div className="explorer">
      <div className="explorer-variants">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
          <strong style={{ fontSize: 13 }}>
            Variants ({selected.length}/{variants.length})
          </strong>
          <span>
            <button className="linklike" onClick={() => setTicked(new Set(variants.map((_, i) => i)))}>
              all
            </button>
            {' · '}
            <button className="linklike" onClick={() => setTicked(new Set())}>
              none
            </button>
          </span>
        </div>
        {variants.map((v, i) => (
          <label className={'variant-row' + (ticked.has(i) ? ' on' : '')} key={v.variant}>
            <input type="checkbox" checked={ticked.has(i)} onChange={() => toggle(i)} />
            <span className="variant-row-body">
              <span className="variant-row-top">
                <strong>#{i + 1}</strong>
                <span>
                  {v.case_count} cases · {(v.share * 100).toFixed(0)}%
                </span>
              </span>
              <span className="bar-track">
                <span className="bar-fill" style={{ width: `${v.share * 100}%` }} />
              </span>
              <span className="variant-mini">
                {v.activities.map((a, j) => (
                  <span key={j}>
                    {j > 0 && <span className="variant-arrow">›</span>}
                    {a}
                  </span>
                ))}
              </span>
            </span>
          </label>
        ))}
        <p className="hint" style={{ marginTop: 10 }}>
          Showing {coveredCases} of {totalCases} cases (
          {totalCases ? ((coveredCases / totalCases) * 100).toFixed(0) : 0}%). Dashed orange
          arcs are rework loops. Edge labels: cases · average wait (overall average for that
          hand-off).
        </p>
      </div>
      <div className="explorer-map">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
          <div className="row" style={{ gap: 6 }}>
            <button className="mode-btn" onClick={() => setZoom(Math.max(0.5, zoom - 0.25))}
                    title="Zoom out">−</button>
            <span className="hint" style={{ minWidth: 44, textAlign: 'center' }}>
              {Math.round(zoom * 100)}%
            </span>
            <button className="mode-btn" onClick={() => setZoom(Math.min(3, zoom + 0.25))}
                    title="Zoom in">+</button>
            <button className="mode-btn" onClick={() => setZoom(1)} title="Fit to width">Fit</button>
          </div>
          <div className="row" style={{ gap: 0 }}>
            <button
              className={'mode-btn' + (mode === 'freq' ? ' on' : '')}
              onClick={() => setMode('freq')}
              title="Edge width and label emphasize case frequency"
            >
              Frequency
            </button>
            <button
              className={'mode-btn' + (mode === 'perf' ? ' on' : '')}
              onClick={() => setMode('perf')}
              title="Edge width, color, and label emphasize average waiting time"
            >
              Performance
            </button>
          </div>
        </div>
        <div
          ref={panRef}
          className="erd-pan"
          onMouseDown={(e) => {
            const el = panRef.current!
            drag.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop }
            e.preventDefault()
          }}
          onMouseMove={(e) => {
            const el = panRef.current
            if (!drag.current || !el) return
            el.scrollLeft = drag.current.left - (e.clientX - drag.current.x)
            el.scrollTop = drag.current.top - (e.clientY - drag.current.y)
          }}
          onMouseUp={() => (drag.current = null)}
          onMouseLeave={() => (drag.current = null)}
        >
          <ProcessMap nodes={nodes} edges={edges} mode={mode} zoom={zoom} />
        </div>
        <p className="hint" style={{ marginTop: 6 }}>Drag the canvas to pan while zoomed.</p>
      </div>
    </div>
  )
}
