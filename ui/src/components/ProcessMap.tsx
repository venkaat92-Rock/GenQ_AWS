// SVG process map (directly-follows graph), laid out top-to-bottom in layers.
// Hand-rolled layout — P2P-scale graphs are small, so no graph library is needed.

export interface MapEdge {
  source: string
  target: string
  count: number
  meanSeconds?: number
}
export interface MapNode {
  activity: string
  events: number
}

const START = '__start__'
const END = '__end__'

const NODE_W = 176
const NODE_H = 48
const COL_GAP = 40
const ROW_GAP = 64
const PAD = 24

interface Laid {
  x: number
  y: number
}

function layout(nodes: MapNode[], edges: MapEdge[]) {
  const ids = [START, ...nodes.map((n) => n.activity), END]
  const out = new Map<string, string[]>()
  ids.forEach((id) => out.set(id, []))
  edges.forEach((e) => out.get(e.source)?.push(e.target))

  // classify back edges (rework loops) with a DFS from START
  const state = new Map<string, number>() // 0 new, 1 in stack, 2 done
  const backEdges = new Set<string>()
  function dfs(u: string) {
    state.set(u, 1)
    for (const v of out.get(u) ?? []) {
      if (state.get(v) === 1) backEdges.add(`${u}→${v}`)
      else if (!state.get(v)) dfs(v)
    }
    state.set(u, 2)
  }
  dfs(START)

  // longest-path layering over forward edges
  const layer = new Map<string, number>()
  ids.forEach((id) => layer.set(id, 0))
  for (let i = 0; i < ids.length; i++) {
    for (const e of edges) {
      if (backEdges.has(`${e.source}→${e.target}`)) continue
      const next = (layer.get(e.source) ?? 0) + 1
      if (next > (layer.get(e.target) ?? 0)) layer.set(e.target, next)
    }
  }
  // END always sits below everything
  const maxLayer = Math.max(...ids.filter((i) => i !== END).map((i) => layer.get(i) ?? 0))
  layer.set(END, maxLayer + 1)

  // group by layer, order within a layer by mean predecessor position
  const byLayer = new Map<number, string[]>()
  ids.forEach((id) => {
    const l = layer.get(id) ?? 0
    byLayer.set(l, [...(byLayer.get(l) ?? []), id])
  })
  const widest = Math.max(...[...byLayer.values()].map((v) => v.length))
  const width = Math.max(widest * (NODE_W + COL_GAP) - COL_GAP + PAD * 2, 480)

  const pos = new Map<string, Laid>()
  const layersSorted = [...byLayer.keys()].sort((a, b) => a - b)
  for (const l of layersSorted) {
    const row = byLayer.get(l)!
    row.sort((a, b) => {
      const px = (id: string) => {
        const preds = edges
          .filter((e) => e.target === id && pos.has(e.source))
          .map((e) => pos.get(e.source)!.x)
        return preds.length ? preds.reduce((s, x) => s + x, 0) / preds.length : width / 2
      }
      return px(a) - px(b)
    })
    const rowWidth = row.length * (NODE_W + COL_GAP) - COL_GAP
    row.forEach((id, i) => {
      pos.set(id, {
        x: (width - rowWidth) / 2 + i * (NODE_W + COL_GAP) + NODE_W / 2,
        y: PAD + l * (NODE_H + ROW_GAP) + NODE_H / 2,
      })
    })
  }
  const height = PAD * 2 + (maxLayer + 2) * (NODE_H + ROW_GAP) - ROW_GAP
  return { pos, backEdges, width, height }
}

function days(seconds?: number): string | null {
  if (!seconds || seconds <= 0) return null
  const d = seconds / 86400
  return d >= 1 ? `${d.toFixed(1)}d` : `${(seconds / 3600).toFixed(1)}h`
}

// heat color for performance mode: slow hand-offs turn red
function heat(fraction: number): string {
  const stops = ['#7d8bb8', '#c98a3d', '#d9534f']
  return fraction > 0.66 ? stops[2] : fraction > 0.33 ? stops[1] : stops[0]
}

export default function ProcessMap(props: {
  nodes: MapNode[]
  edges: MapEdge[]
  mode?: 'freq' | 'perf'
  zoom?: number
}) {
  const { nodes, edges } = props
  const mode = props.mode ?? 'freq'
  const zoom = props.zoom ?? 1
  if (nodes.length === 0) {
    return <p className="hint">Select at least one variant to draw the process map.</p>
  }
  const { pos, backEdges, width, height } = layout(nodes, edges)
  const maxCount = Math.max(...edges.map((e) => e.count), 1)
  const maxMean = Math.max(...edges.map((e) => e.meanSeconds ?? 0), 1)
  const strokeFor = (e: MapEdge) =>
    mode === 'perf'
      ? 1.5 + 6 * ((e.meanSeconds ?? 0) / maxMean)
      : 1.5 + 6 * (e.count / maxCount)

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      style={
        zoom === 1
          ? { width: '100%', maxWidth: width, display: 'block', margin: '0 auto' }
          : { width: width * zoom, display: 'block', margin: '0 auto' }
      }
      role="img"
      aria-label="Process map"
    >
      <defs>
        <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="11" markerHeight="11" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
          <path d="M0,0 L8,4 L0,8 z" fill="#7d8bb8" />
        </marker>
        <marker id="arrow-rework" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="11" markerHeight="11" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
          <path d="M0,0 L8,4 L0,8 z" fill="#d97c00" />
        </marker>
      </defs>

      {edges.map((e) => {
        const a = pos.get(e.source)
        const b = pos.get(e.target)
        if (!a || !b) return null
        const isBack = backEdges.has(`${e.source}→${e.target}`)
        const key = `${e.source}→${e.target}`
        const w = strokeFor(e)
        const dur = days(e.meanSeconds)
        const stroke = isBack
          ? '#d97c00'
          : mode === 'perf'
            ? heat((e.meanSeconds ?? 0) / maxMean)
            : '#7d8bb8'
        const label =
          mode === 'perf' ? `${dur ?? '—'} · ${e.count}` : `${e.count}${dur ? ` · ${dur}` : ''}`
        let d: string
        let lx: number
        let ly: number
        if (isBack) {
          // rework loop: arc out on the right and back up
          const off = NODE_W / 2 + 46
          const x1 = a.x + NODE_W / 2
          const y1 = a.y
          const x2 = b.x + NODE_W / 2
          const y2 = b.y
          d = `M ${x1} ${y1} C ${x1 + off} ${y1}, ${x2 + off} ${y2}, ${x2} ${y2}`
          lx = Math.max(x1, x2) + off - 8
          ly = (y1 + y2) / 2
        } else {
          const x1 = a.x
          const y1 = a.y + (e.source === START ? 10 : NODE_H / 2)
          const x2 = b.x
          const y2 = b.y - (e.target === END ? 10 : NODE_H / 2)
          const bend = (y2 - y1) / 2
          d = `M ${x1} ${y1} C ${x1} ${y1 + bend}, ${x2} ${y2 - bend}, ${x2} ${y2}`
          lx = (x1 + x2) / 2 + (x1 === x2 ? 10 : 0)
          ly = (y1 + y2) / 2
        }
        return (
          <g key={key}>
            <path
              d={d}
              fill="none"
              stroke={stroke}
              strokeWidth={w}
              strokeDasharray={isBack ? '6 4' : undefined}
              markerEnd={isBack ? 'url(#arrow-rework)' : 'url(#arrow)'}
              opacity={0.85}
            >
              <title>
                {e.source === START ? 'Start' : e.source} → {e.target === END ? 'End' : e.target}: {e.count} cases
                {dur ? `, avg ${dur}` : ''}
              </title>
            </path>
            <text
              x={lx}
              y={ly}
              fontSize="11"
              fontWeight="700"
              fill={isBack ? '#935f00' : '#3b4a75'}
              textAnchor="middle"
              paintOrder="stroke"
              stroke="#ffffff"
              strokeWidth="3.5"
            >
              {label}
            </text>
          </g>
        )
      })}

      {/* start / end markers */}
      {[START, END].map((id) => {
        const p = pos.get(id)
        if (!p) return null
        return (
          <g key={id}>
            <circle cx={p.x} cy={p.y} r={10} fill={id === START ? '#0d7a4f' : '#14213d'} />
            <text x={p.x} y={p.y + 24} fontSize="11" fill="#5b6478" textAnchor="middle">
              {id === START ? 'Start' : 'End'}
            </text>
          </g>
        )
      })}

      {nodes.map((n) => {
        const p = pos.get(n.activity)
        if (!p) return null
        return (
          <g key={n.activity}>
            <rect
              x={p.x - NODE_W / 2}
              y={p.y - NODE_H / 2}
              width={NODE_W}
              height={NODE_H}
              rx={9}
              fill="#e8ecff"
              stroke="#2743e0"
              strokeWidth={1.4}
            />
            <text x={p.x} y={p.y - 2} fontSize="12.5" fontWeight="700" fill="#14213d" textAnchor="middle">
              {n.activity}
            </text>
            <text x={p.x} y={p.y + 14} fontSize="10.5" fill="#5b6478" textAnchor="middle">
              {n.events} events
            </text>
          </g>
        )
      })}
    </svg>
  )
}

export { START, END }
