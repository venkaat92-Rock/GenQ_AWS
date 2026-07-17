import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'

interface DmTable {
  name: string
  role: 'source' | 'event_log'
  row_count: number
  columns: string[]
  identifier: string | null
  identifier_origin: 'agent' | 'human' | null
  identifier_candidates: string[]
}
interface DmRel {
  from_table: string
  from_column: string
  to_table: string
  to_column: string
  origin: 'agent' | 'human'
  via: string
}
interface DataModel {
  tables: DmTable[]
  relationships: DmRel[]
  missing_identifiers: string[]
  updated_by: string | null
}

const BOX_W = 180
const BOX_H = 58
const H_GAP = 26
const V_GAP = 70

function layout(tables: DmTable[], rels: DmRel[]) {
  const adj = new Map<string, Set<string>>()
  tables.forEach((t) => adj.set(t.name, new Set()))
  rels.forEach((r) => {
    adj.get(r.from_table)?.add(r.to_table)
    adj.get(r.to_table)?.add(r.from_table)
  })
  const center = tables.find((t) => t.role === 'event_log')?.name ?? tables[0]?.name
  const dist = new Map<string, number>([[center, 0]])
  const queue = [center]
  while (queue.length) {
    const u = queue.shift()!
    for (const v of adj.get(u) ?? []) {
      if (!dist.has(v)) {
        dist.set(v, (dist.get(u) ?? 0) + 1)
        queue.push(v)
      }
    }
  }
  const maxD = Math.max(0, ...dist.values())
  tables.forEach((t) => {
    if (!dist.has(t.name)) dist.set(t.name, maxD + 1)
  })
  const rows = new Map<number, string[]>()
  tables.forEach((t) => {
    const d = dist.get(t.name)!
    rows.set(d, [...(rows.get(d) ?? []), t.name])
  })
  const widest = Math.max(...[...rows.values()].map((r) => r.length))
  const width = Math.max(widest * (BOX_W + H_GAP) + H_GAP, 640)
  const pos = new Map<string, { x: number; y: number }>()
  const sorted = [...rows.keys()].sort((a, b) => a - b)
  sorted.forEach((d, rowIndex) => {
    const row = rows.get(d)!.sort()
    const rowWidth = row.length * (BOX_W + H_GAP) - H_GAP
    row.forEach((name, i) => {
      pos.set(name, {
        x: (width - rowWidth) / 2 + i * (BOX_W + H_GAP) + BOX_W / 2,
        y: 30 + rowIndex * (BOX_H + V_GAP) + BOX_H / 2,
      })
    })
  })
  const height = 60 + sorted.length * (BOX_H + V_GAP) - V_GAP
  return { pos, width, height }
}

export default function DataModelView(props: { canEdit: boolean }) {
  const [model, setModel] = useState<DataModel | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [reviewer, setReviewer] = useState('')
  const [draftRels, setDraftRels] = useState<DmRel[] | null>(null)
  const [draftIds, setDraftIds] = useState<Record<string, string | null>>({})
  const [add, setAdd] = useState({ ft: '', fc: '', tt: '', tc: '' })
  const [zoom, setZoom] = useState(1)
  const panRef = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null)

  const refresh = () =>
    api<DataModel>('/api/datamodel')
      .then((m) => {
        setModel(m)
        setDraftRels(null)
        setDraftIds({})
      })
      .catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  const rels = draftRels ?? model?.relationships ?? []
  const geometry = useMemo(
    () => (model && model.tables.length > 0 ? layout(model.tables, rels) : null),
    [model, rels],
  )
  if (!model) return error ? <div className="error-banner">{error}</div> : null
  if (!geometry) {
    return (
      <div className="card" style={{ marginTop: 16 }}>
        <h3>Data model</h3>
        <p className="hint">No tables staged yet — upload source data on the Data page first.</p>
      </div>
    )
  }

  const dirty = draftRels !== null || Object.keys(draftIds).length > 0
  const byName = new Map(model.tables.map((t) => [t.name, t]))

  const removeRel = (r: DmRel) =>
    setDraftRels(rels.filter((x) => x !== r))

  const addRel = () => {
    if (!add.ft || !add.fc || !add.tt || !add.tc) return
    setDraftRels([
      ...rels,
      { from_table: add.ft, from_column: add.fc, to_table: add.tt, to_column: add.tc,
        origin: 'human', via: 'added in UI' },
    ])
    setAdd({ ft: '', fc: '', tt: '', tc: '' })
  }

  async function save() {
    setError('')
    try {
      const saved = await api<DataModel>('/api/datamodel', {
        method: 'PUT',
        body: JSON.stringify({
          relationships: draftRels ?? undefined,
          identifiers: Object.keys(draftIds).length ? draftIds : undefined,
          updated_by: reviewer,
        }),
      })
      setModel(saved)
      setDraftRels(null)
      setDraftIds({})
      setNotice('Data model saved.')
    } catch (e: any) {
      setError(e.message)
    }
  }

  async function rederive() {
    setError('')
    try {
      const fresh = await api<DataModel>('/api/datamodel/rederive', { method: 'POST' })
      setModel(fresh)
      setDraftRels(null)
      setDraftIds({})
      setNotice('Model re-derived by the agent (human edits discarded).')
    } catch (e: any) {
      setError(e.message)
    }
  }

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3>
        Data model — tables &amp; foreign keys{' '}
        <span className="badge status">auto-derived · {model.updated_by ?? 'agent'}</span>
      </h3>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}
      {model.missing_identifiers.length > 0 && (
        <div className="warn-banner">
          <strong>Missing identifiers</strong> — no unique-row column found for:{' '}
          {model.missing_identifiers.join(', ')}. Identifiers enable incremental (delta)
          loads later; set one below if you know the business key.
        </div>
      )}

      <div className="explorer-map" style={{ marginBottom: 14 }}>
        <div className="row" style={{ justifyContent: 'flex-end', gap: 6, marginBottom: 6 }}>
          <button className="mode-btn" onClick={() => setZoom(Math.max(0.5, zoom - 0.25))} title="Zoom out">
            −
          </button>
          <span className="hint" style={{ minWidth: 44, textAlign: 'center' }}>
            {Math.round(zoom * 100)}%
          </span>
          <button className="mode-btn" onClick={() => setZoom(Math.min(3, zoom + 0.25))} title="Zoom in">
            +
          </button>
          <button className="mode-btn" onClick={() => setZoom(1)} title="Fit to width">
            Fit
          </button>
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
        <svg
          viewBox={`0 0 ${geometry.width} ${geometry.height}`}
          style={
            zoom === 1
              ? { width: '100%', maxWidth: geometry.width, display: 'block', margin: '0 auto' }
              : { width: geometry.width * zoom, display: 'block', margin: '0 auto' }
          }
        >
          {rels.map((r, i) => {
            const a = geometry.pos.get(r.from_table)
            const b = geometry.pos.get(r.to_table)
            if (!a || !b) return null
            const label =
              r.from_column === r.to_column
                ? r.from_column
                : `${r.from_column} = ${r.to_column}`
            return (
              <g key={i}>
                <line
                  x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={r.origin === 'human' ? '#0d7a4f' : '#93a2cc'}
                  strokeWidth={1.4}
                  strokeDasharray={r.via === 'shared key column' ? '5 4' : undefined}
                >
                  <title>{`${r.from_table}.${r.from_column} = ${r.to_table}.${r.to_column} (${r.via}, ${r.origin})`}</title>
                </line>
                <text
                  x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 3}
                  fontSize="9.5" fill="#5b6478" textAnchor="middle"
                  paintOrder="stroke" stroke="#fafbfe" strokeWidth="3"
                >
                  {label}
                </text>
              </g>
            )
          })}
          {model.tables.map((t) => {
            const p = geometry.pos.get(t.name)
            if (!p) return null
            const isLog = t.role === 'event_log'
            return (
              <g key={t.name}>
                <rect
                  x={p.x - BOX_W / 2} y={p.y - BOX_H / 2} width={BOX_W} height={BOX_H}
                  rx={8}
                  fill={isLog ? '#2743e0' : '#ffffff'}
                  stroke={isLog ? '#2743e0' : '#c9d3ea'}
                  strokeWidth={1.4}
                />
                <text
                  x={p.x} y={p.y - 8} fontSize="12" fontWeight="700"
                  fill={isLog ? '#ffffff' : '#14213d'} textAnchor="middle"
                >
                  {t.name}
                </text>
                <text
                  x={p.x} y={p.y + 8} fontSize="9.5"
                  fill={isLog ? '#c8d2ec' : '#5b6478'} textAnchor="middle"
                >
                  {t.row_count} rows
                  {t.identifier ? ` · id: ${t.identifier}` : ' · no identifier'}
                </text>
                <text
                  x={p.x} y={p.y + 21} fontSize="8.5"
                  fill={isLog ? '#aab8e8' : '#8b95ad'} textAnchor="middle"
                >
                  {t.columns.slice(0, 4).join(', ')}
                  {t.columns.length > 4 ? ', …' : ''}
                </text>
              </g>
            )
          })}
        </svg>
        </div>
        <p className="hint" style={{ textAlign: 'center' }}>
          Solid lines: joins the mapping executes · dashed: shared-key proposals · green:
          added by a human. Drag the canvas to pan while zoomed.
        </p>
      </div>

      {props.canEdit && (
        <>
          <h3 style={{ marginBottom: 8 }}>Foreign keys ({rels.length})</h3>
          <table>
            <thead>
              <tr>
                <th>From</th><th>To</th><th>Why</th><th>Origin</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rels.map((r, i) => (
                <tr key={i}>
                  <td><code className="inline">{r.from_table}.{r.from_column}</code></td>
                  <td><code className="inline">{r.to_table}.{r.to_column}</code></td>
                  <td className="hint">{r.via}</td>
                  <td><span className={'badge ' + (r.origin === 'human' ? 'high' : 'status')}>{r.origin}</span></td>
                  <td>
                    <button className="linklike" onClick={() => removeRel(r)}>✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="row" style={{ marginTop: 10 }}>
            <select value={add.ft} onChange={(e) => setAdd({ ...add, ft: e.target.value, fc: '' })}>
              <option value="">from table…</option>
              {model.tables.map((t) => <option key={t.name}>{t.name}</option>)}
            </select>
            <select value={add.fc} onChange={(e) => setAdd({ ...add, fc: e.target.value })}>
              <option value="">column…</option>
              {(byName.get(add.ft)?.columns ?? []).map((c) => <option key={c}>{c}</option>)}
            </select>
            <span className="hint">=</span>
            <select value={add.tt} onChange={(e) => setAdd({ ...add, tt: e.target.value, tc: '' })}>
              <option value="">to table…</option>
              {model.tables.map((t) => <option key={t.name}>{t.name}</option>)}
            </select>
            <select value={add.tc} onChange={(e) => setAdd({ ...add, tc: e.target.value })}>
              <option value="">column…</option>
              {(byName.get(add.tt)?.columns ?? []).map((c) => <option key={c}>{c}</option>)}
            </select>
            <button className="ghost" onClick={addRel}
                    disabled={!add.ft || !add.fc || !add.tt || !add.tc}>
              Add foreign key
            </button>
          </div>

          <h3 style={{ margin: '18px 0 8px' }}>Identifiers (unique-row keys)</h3>
          <div className="row" style={{ gap: 14, flexWrap: 'wrap' }}>
            {model.tables.filter((t) => t.role === 'source').map((t) => (
              <label key={t.name} className="row" style={{ gap: 6, fontSize: 13 }}>
                <code className="inline">{t.name}</code>
                <select
                  value={draftIds[t.name] !== undefined ? draftIds[t.name] ?? '' : t.identifier ?? ''}
                  onChange={(e) =>
                    setDraftIds({ ...draftIds, [t.name]: e.target.value || null })
                  }
                >
                  <option value="">— none —</option>
                  {[...new Set([...(t.identifier ? [t.identifier] : []), ...t.identifier_candidates, ...t.columns])]
                    .map((c) => <option key={c}>{c}</option>)}
                </select>
                {t.identifier_origin && (
                  <span className={'badge ' + (t.identifier_origin === 'human' ? 'high' : 'status')}>
                    {t.identifier_origin}
                  </span>
                )}
              </label>
            ))}
          </div>

          <div className="row" style={{ marginTop: 14 }}>
            <input
              type="text"
              placeholder="Your name (SME sign-off)"
              style={{ maxWidth: 220 }}
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
            />
            <button className="primary" onClick={save} disabled={!dirty || !reviewer.trim()}>
              Save data model
            </button>
            <button className="ghost" onClick={rederive}>
              Re-derive from agent
            </button>
          </div>
        </>
      )}
    </div>
  )
}
