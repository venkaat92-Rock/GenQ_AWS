import { useEffect, useState } from 'react'
import { api } from '../api'

export interface KpiSeriesPoint {
  label: string
  value: number
  cases: number
}
export interface CustomKpi {
  id: string
  name: string
  value: number | null
  unit: string
  series?: KpiSeriesPoint[]
  note?: string
  definition?: { viz?: string; group_by?: string; trend?: string }
}

const METRICS = [
  { kind: 'case_count', label: 'Number of cases' },
  { kind: 'cycle_time', label: 'Cycle time (days)' },
  { kind: 'transition_wait', label: 'Hand-off wait between two activities' },
  { kind: 'case_share', label: 'Share of cases (%)' },
  { kind: 'attribute_agg', label: 'Attribute total / average' },
]

// which representations fit which breakdown — mirrors the Celonis component palette
const VIZ_FOR: Record<string, { id: string; label: string }[]> = {
  none: [{ id: 'card', label: 'KPI card' }],
  group_by: [{ id: 'bar', label: 'Bar chart' }, { id: 'table', label: 'Table' }],
  trend: [{ id: 'line', label: 'Line (trend)' }, { id: 'column', label: 'Column' }],
}

export default function KpiBuilder(props: { onSaved: () => void; onClose: () => void }) {
  const [activities, setActivities] = useState<string[]>([])
  const [attributes, setAttributes] = useState<string[]>([])
  const [name, setName] = useState('')
  const [kind, setKind] = useState('cycle_time')
  const [agg, setAgg] = useState('median')
  const [fromA, setFromA] = useState('')
  const [toA, setToA] = useState('')
  const [shareMode, setShareMode] = useState('rework')
  const [containsA, setContainsA] = useState('')
  const [attribute, setAttribute] = useState('')
  const [attrAgg, setAttrAgg] = useState('sum')
  const [breakdown, setBreakdown] = useState<'none' | 'group_by' | 'trend'>('none')
  const [groupAttr, setGroupAttr] = useState('')
  const [trendFreq, setTrendFreq] = useState('month')
  const [viz, setViz] = useState('card')
  const [preview, setPreview] = useState<CustomKpi | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api<{ activities: string[]; attributes: string[] }>('/api/kpis/options')
      .then((r) => {
        setActivities(r.activities)
        setAttributes(r.attributes)
        if (r.attributes.length) {
          setAttribute(r.attributes[0])
          setGroupAttr(r.attributes[0])
        }
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    setViz(VIZ_FOR[breakdown][0].id)
    setPreview(null)
  }, [breakdown])

  const autoName = () => {
    const metric = METRICS.find((m) => m.kind === kind)?.label ?? kind
    let base = metric
    if (kind === 'cycle_time' && fromA && toA) base = `${agg} days ${fromA} → ${toA}`
    else if (kind === 'cycle_time') base = `${agg} cycle time`
    else if (kind === 'transition_wait') base = `Wait ${fromA} → ${toA}`
    else if (kind === 'case_share') {
      base = shareMode === 'rework' ? 'Rework rate' : `Cases with ${containsA}`
    } else if (kind === 'attribute_agg') base = `${attrAgg} of ${attribute}`
    if (breakdown === 'group_by') base += ` by ${groupAttr}`
    if (breakdown === 'trend') base += ` per ${trendFreq}`
    return base.charAt(0).toUpperCase() + base.slice(1)
  }

  const definition = (): Record<string, unknown> => {
    const d: Record<string, unknown> = { kind, viz }
    if (kind === 'cycle_time') {
      d.agg = agg
      if (fromA) d.from_activity = fromA
      if (toA) d.to_activity = toA
    }
    if (kind === 'transition_wait') {
      d.agg = agg
      d.from_activity = fromA
      d.to_activity = toA
    }
    if (kind === 'case_share') {
      if (shareMode === 'rework') d.rework = true
      else d.contains_activity = containsA
    }
    if (kind === 'attribute_agg') {
      d.attribute = attribute
      d.agg = attrAgg
    }
    if (breakdown === 'group_by') d.group_by = groupAttr
    if (breakdown === 'trend') d.trend = trendFreq
    return d
  }

  const incomplete =
    (kind === 'transition_wait' && (!fromA || !toA)) ||
    (kind === 'case_share' && shareMode === 'contains' && !containsA) ||
    (kind === 'attribute_agg' && !attribute) ||
    (breakdown === 'group_by' && !groupAttr)

  const doPreview = async () => {
    setBusy(true)
    setError('')
    try {
      const r = await api<CustomKpi>('/api/kpis/preview', {
        method: 'POST',
        body: JSON.stringify({ name: 'preview', definition: definition() }),
      })
      setPreview(r)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    setBusy(true)
    setError('')
    try {
      await api('/api/kpis', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim() || autoName(),
                               definition: definition() }),
      })
      props.onSaved()
      props.onClose()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const actSelect = (value: string, set: (v: string) => void, blank: string) => (
    <select value={value} onChange={(e) => set(e.target.value)}>
      <option value="">{blank}</option>
      {activities.map((a) => <option key={a} value={a}>{a}</option>)}
    </select>
  )

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h3 style={{ margin: 0 }}>New KPI</h3>
        <button className="linklike" onClick={props.onClose}>Close</button>
      </div>
      <p className="hint">
        Pick what to measure and how to represent it — same building blocks as the
        co-pilot, no chat needed. Every KPI is computed from the event log and
        respects the dashboard filters.
      </p>
      {error && <div className="error-banner">{error}</div>}

      <div className="row" style={{ flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label className="editor-field">Metric
          <select value={kind} onChange={(e) => { setKind(e.target.value); setPreview(null) }}>
            {METRICS.map((m) => <option key={m.kind} value={m.kind}>{m.label}</option>)}
          </select>
        </label>
        {(kind === 'cycle_time' || kind === 'transition_wait') && (
          <>
            <label className="editor-field">Aggregation
              <select value={agg} onChange={(e) => setAgg(e.target.value)}>
                <option value="median">median</option>
                <option value="mean">mean</option>
                <option value="p90">p90</option>
              </select>
            </label>
            <label className="editor-field">
              From {kind === 'cycle_time' ? '(case start when empty)' : ''}
              {actSelect(fromA, setFromA, kind === 'cycle_time' ? 'case start' : 'select…')}
            </label>
            <label className="editor-field">
              To {kind === 'cycle_time' ? '(case end when empty)' : ''}
              {actSelect(toA, setToA, kind === 'cycle_time' ? 'case end' : 'select…')}
            </label>
          </>
        )}
        {kind === 'case_share' && (
          <>
            <label className="editor-field">Counting
              <select value={shareMode} onChange={(e) => setShareMode(e.target.value)}>
                <option value="rework">cases with rework</option>
                <option value="contains">cases containing an activity</option>
              </select>
            </label>
            {shareMode === 'contains' && (
              <label className="editor-field">Activity
                {actSelect(containsA, setContainsA, 'select…')}
              </label>
            )}
          </>
        )}
        {kind === 'attribute_agg' && (
          <>
            <label className="editor-field">Attribute
              <select value={attribute} onChange={(e) => setAttribute(e.target.value)}>
                {attributes.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </label>
            <label className="editor-field">Aggregation
              <select value={attrAgg} onChange={(e) => setAttrAgg(e.target.value)}>
                <option value="sum">sum</option>
                <option value="mean">mean</option>
                <option value="median">median</option>
              </select>
            </label>
          </>
        )}
      </div>

      <div className="row" style={{ flexWrap: 'wrap', alignItems: 'flex-end', marginTop: 10 }}>
        <label className="editor-field">Breakdown
          <select value={breakdown}
                  onChange={(e) => setBreakdown(e.target.value as any)}>
            <option value="none">none — single number</option>
            <option value="group_by">by attribute (categories)</option>
            <option value="trend">over time (trend)</option>
          </select>
        </label>
        {breakdown === 'group_by' && (
          <label className="editor-field">Attribute
            <select value={groupAttr} onChange={(e) => setGroupAttr(e.target.value)}>
              {attributes.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </label>
        )}
        {breakdown === 'trend' && (
          <label className="editor-field">Bucket
            <select value={trendFreq} onChange={(e) => setTrendFreq(e.target.value)}>
              <option value="month">per month</option>
              <option value="week">per week</option>
            </select>
          </label>
        )}
        <label className="editor-field">Representation
          <select value={viz} onChange={(e) => setViz(e.target.value)}>
            {VIZ_FOR[breakdown].map((v) => (
              <option key={v.id} value={v.id}>{v.label}</option>
            ))}
          </select>
        </label>
        <label className="editor-field" style={{ minWidth: 260, flex: 1 }}>Name
          <input type="text" placeholder={autoName()} value={name}
                 onChange={(e) => setName(e.target.value)} />
        </label>
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <button onClick={doPreview} disabled={busy || incomplete}>Preview</button>
        <button className="primary" onClick={save} disabled={busy || incomplete}>
          Save to dashboard
        </button>
        {incomplete && <span className="hint">Complete the metric fields first.</span>}
      </div>

      {preview && (
        <div className="subcard" style={{ marginTop: 12 }}>
          <strong>{name.trim() || autoName()}</strong>
          <KpiViz kpi={{ ...preview, id: 'preview', name: name.trim() || autoName(),
                         definition: { viz } }} />
        </div>
      )}
    </div>
  )
}

// ---- representations -------------------------------------------------------------------

export function KpiViz({ kpi }: { kpi: CustomKpi }) {
  const viz = kpi.definition?.viz ?? (kpi.series ? 'bar' : 'card')
  if (!kpi.series || viz === 'card') {
    return (
      <div className="value" style={{ fontSize: 26, fontWeight: 750 }}>
        {kpi.value ?? '—'}
        <span style={{ fontSize: 14, fontWeight: 600 }}> {kpi.unit}</span>
      </div>
    )
  }
  const s = kpi.series
  if (viz === 'table') {
    return (
      <table style={{ marginTop: 6 }}>
        <thead><tr><th>Category</th><th>Value ({kpi.unit})</th><th>Cases</th></tr></thead>
        <tbody>
          {s.map((p) => (
            <tr key={p.label}>
              <td>{p.label}</td><td>{p.value}</td><td className="hint">{p.cases}</td>
            </tr>
          ))}
        </tbody>
      </table>
    )
  }
  const max = Math.max(...s.map((p) => p.value), 0.0001)
  if (viz === 'bar') {
    return (
      <div style={{ marginTop: 8 }}>
        {s.map((p) => (
          <div key={p.label} className="row" style={{ gap: 8, marginBottom: 4 }}>
            <span style={{ width: 130, fontSize: 12, textAlign: 'right',
                           overflow: 'hidden', textOverflow: 'ellipsis',
                           whiteSpace: 'nowrap' }} title={p.label}>{p.label}</span>
            <div style={{ flex: 1, background: '#eef1f8', borderRadius: 4, height: 16 }}>
              <div style={{ width: `${(p.value / max) * 100}%`, height: '100%',
                            background: 'var(--accent)', borderRadius: 4 }} />
            </div>
            <span style={{ width: 70, fontSize: 12, fontWeight: 650 }}>
              {p.value} {kpi.unit}
            </span>
          </div>
        ))}
        {kpi.note && <p className="hint">{kpi.note}</p>}
      </div>
    )
  }
  // column / line — a compact SVG over the buckets
  const W = 460
  const H = 130
  const pad = 24
  const step = (W - pad * 2) / Math.max(s.length, 1)
  const y = (v: number) => H - pad - (v / max) * (H - pad * 2)
  return (
    <div style={{ overflowX: 'auto' }}>
      <svg viewBox={`0 0 ${W} ${H + 18}`} style={{ width: '100%', maxWidth: W }}>
        {viz === 'column' && s.map((p, i) => (
          <rect key={p.label} x={pad + i * step + step * 0.15} y={y(p.value)}
                width={step * 0.7} height={H - pad - y(p.value)}
                fill="var(--accent)" rx={2}>
            <title>{p.label}: {p.value} {kpi.unit} ({p.cases} cases)</title>
          </rect>
        ))}
        {viz === 'line' && (
          <>
            <polyline fill="none" stroke="var(--accent)" strokeWidth={2}
                      points={s.map((p, i) =>
                        `${pad + i * step + step / 2},${y(p.value)}`).join(' ')} />
            {s.map((p, i) => (
              <circle key={p.label} cx={pad + i * step + step / 2} cy={y(p.value)}
                      r={3} fill="var(--accent)">
                <title>{p.label}: {p.value} {kpi.unit} ({p.cases} cases)</title>
              </circle>
            ))}
          </>
        )}
        {s.map((p, i) => (
          (s.length <= 8 || i % Math.ceil(s.length / 8) === 0) && (
            <text key={p.label} x={pad + i * step + step / 2} y={H + 10}
                  fontSize={9} fill="#5b6478" textAnchor="middle">{p.label}</text>
          )
        ))}
      </svg>
    </div>
  )
}
