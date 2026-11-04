import { useEffect, useState } from 'react'
import { api } from '../api'
import FilterBar, { EMPTY_FILTERS, FilterState, toQuery } from '../components/FilterBar'

/* The "Improve" step: the dashboard says what IS happening, this page says what to
   DO about it. Two halves, deliberately on one page rather than two nav items —
   an opportunity and the drift that created it are the same conversation.

   Opportunities  (Advisor) — ranked, costed, each citing the measurement behind it.
   Signals        (drift)   — what moved since a human froze a baseline. */

interface Opportunity {
  id: string
  title: string
  detail: string
  category: string
  confidence: 'high' | 'medium' | 'low'
  based_on: string
  evidence: Record<string, any>
  affected_cases: number
  labour_hours: number | null
  labour_value: number | null
  cycle_days: number | null
}
interface Advisor {
  opportunities: Opportunity[]
  assumptions: { hourly_rate: number; hours_per_touch: number; currency: string }
  totals: { labour_hours: number; labour_value: number; cycle_days: number; currency: string }
}
interface Signal {
  id: string
  kind: 'numeric' | 'structural'
  title: string
  detail: string
  severity: 'critical' | 'warn' | 'info'
  based_on: string
  change: number | null
}
interface SignalsResp {
  baseline: { captured_at?: string; captured_by?: string } | null
  current: { cases: number; events: number; median_cycle_days: number }
  signals: Signal[]
}

const money = (v: number, ccy: string) =>
  `${ccy} ${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`

export default function ImprovePage() {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS)
  const [advisor, setAdvisor] = useState<Advisor | null>(null)
  const [signals, setSignals] = useState<SignalsResp | null>(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [rate, setRate] = useState('65')
  const [touch, setTouch] = useState('0.5')
  const [capturedBy, setCapturedBy] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')

  const q = toQuery(filters)

  function loadAdvisor() {
    const sep = q ? '&' : '?'
    api<Advisor>(`/api/analysis/advisor${q}${sep}hourly_rate=${rate || 0}&hours_per_touch=${touch || 0}`)
      .then(setAdvisor)
      .catch((e) => setError(e.message))
  }

  useEffect(loadAdvisor, [q])
  useEffect(() => {
    api<SignalsResp>('/api/signals').then(setSignals).catch((e) => setError(e.message))
  }, [])

  async function captureBaseline() {
    setBusy(true); setError(''); setNotice('')
    try {
      await api('/api/signals/baseline', {
        method: 'POST', body: JSON.stringify({ captured_by: capturedBy }),
      })
      setSignals(await api<SignalsResp>('/api/signals'))
      setNotice('Baseline captured. Future changes will be reported here as signals.')
      setCapturedBy('')
    } catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <>
      <h1>
        Improve
        {signals && signals.signals.length > 0 && (
          <span className="badge low">{signals.signals.length} signals</span>
        )}
      </h1>
      <p className="lead">
        The dashboard tells you what the process is doing. This page tells you what is worth
        changing, and what has changed since you last looked. Every figure names the
        measurement it came from — nothing here is a black box.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      {/* ---------- Opportunities ---------- */}
      <h3 style={{ margin: '20px 0 8px' }}>Opportunities</h3>
      <FilterBar value={filters} onChange={setFilters} />

      <div className="card">
        <div className="row" style={{ alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <label className="editor-field">
            Cost per hour
            <input type="number" min="0" step="5" value={rate}
                   onChange={(e) => setRate(e.target.value)} style={{ width: 100 }} />
          </label>
          <label className="editor-field">
            Hours per manual touch
            <input type="number" min="0" step="0.25" value={touch}
                   onChange={(e) => setTouch(e.target.value)} style={{ width: 100 }} />
          </label>
          <button className="ghost" onClick={loadAdvisor}>Recalculate</button>
          <span className="hint" style={{ flex: 1, minWidth: 240 }}>
            These are assumptions, not measurements. Correct them to your own rates — every
            value below is recomputed from exactly these two numbers.
          </span>
        </div>
      </div>

      {advisor && advisor.opportunities.length > 0 && (
        <div className="card">
          <div className="row" style={{ gap: 26, flexWrap: 'wrap' }}>
            <div>
              <div className="filter-label">Avoidable work</div>
              <strong style={{ fontSize: 20 }}>{advisor.totals.labour_hours.toLocaleString()} h</strong>
            </div>
            <div>
              <div className="filter-label">Estimated value</div>
              <strong style={{ fontSize: 20 }}>
                {money(advisor.totals.labour_value, advisor.totals.currency)}
              </strong>
            </div>
            <div>
              <div className="filter-label">Calendar time recoverable</div>
              <strong style={{ fontSize: 20 }}>{advisor.totals.cycle_days} days</strong>
            </div>
          </div>
          <p className="hint" style={{ marginTop: 10 }}>
            Labour and calendar time are kept apart on purpose. Recovering ten days of cycle
            time is not the same as saving ten days of someone's work, and adding them
            together would overstate the case.
          </p>
        </div>
      )}

      {advisor?.opportunities.map((o) => (
        <div className="card" key={o.id}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <h3 style={{ margin: 0 }}>{o.title}</h3>
            <span className={'badge ' + o.confidence}>{o.confidence}</span>
          </div>
          <p style={{ marginTop: 8 }}>{o.detail}</p>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
            {o.labour_value != null && advisor && (
              <span className="chip">{money(o.labour_value, advisor.assumptions.currency)}/period</span>
            )}
            {o.labour_hours != null && <span className="chip">{o.labour_hours} h of manual work</span>}
            {o.cycle_days != null && <span className="chip">{o.cycle_days} days of cycle time</span>}
            <span className="chip">{o.affected_cases} cases</span>
            <span className="chip">{o.category}</span>
          </div>
          <p className="hint" style={{ marginTop: 10 }}>Based on: {o.based_on}</p>
          <button className="ghost" style={{ marginTop: 6 }}
                  onClick={() => setOpen(open === o.id ? null : o.id)}>
            {open === o.id ? 'Hide the numbers' : 'Show the numbers'}
          </button>
          {open === o.id && (
            <pre className="sql-explain" style={{ marginTop: 8 }}>
              {JSON.stringify(o.evidence, null, 2)}
            </pre>
          )}
        </div>
      ))}

      {advisor && advisor.opportunities.length === 0 && (
        <div className="card">
          <p className="hint">
            No opportunities stand out in this slice — no dominant bottleneck, no concentrated
            rework. Widen the filters or load more history.
          </p>
        </div>
      )}

      {/* ---------- Signals ---------- */}
      <h3 style={{ margin: '26px 0 8px' }}>Signals</h3>
      <div className="card">
        {signals?.baseline?.captured_at ? (
          <p className="hint">
            Baseline captured {new Date(signals.baseline.captured_at).toLocaleString()}
            {signals.baseline.captured_by ? ` by ${signals.baseline.captured_by}` : ''}. The log
            now holds {signals.current.cases.toLocaleString()} cases
            ({signals.current.events.toLocaleString()} events).
          </p>
        ) : (
          <p className="hint">
            No baseline yet. Freeze today's numbers and this page will report what moves
            afterwards — a drifting process, or a source system that changed shape.
          </p>
        )}
        <div className="row" style={{ marginTop: 8, flexWrap: 'wrap' }}>
          <label className="editor-field">
            Your name
            <input type="text" placeholder="who is freezing this" value={capturedBy}
                   onChange={(e) => setCapturedBy(e.target.value)} />
          </label>
          <button className="primary" disabled={busy || !capturedBy.trim()}
                  onClick={captureBaseline}>
            {signals?.baseline ? 'Re-baseline from today' : 'Capture baseline'}
          </button>
        </div>
      </div>

      {signals?.signals.length === 0 && signals.baseline && (
        <div className="card">
          <p className="hint">
            Nothing has moved more than 10% since the baseline. Silence here is the intended
            state — signals only appear when a change is big enough to act on.
          </p>
        </div>
      )}

      {signals?.signals.map((s) => (
        <div className={'insight ' + (s.severity === 'info' ? '' : 'warn')} key={s.id}>
          <strong>{s.title}</strong>
          {s.kind === 'structural' && <span className="badge low" style={{ marginLeft: 8 }}>structure</span>}
          <div style={{ marginTop: 4 }}>{s.detail}</div>
          <div className="hint" style={{ marginTop: 4 }}>Based on: {s.based_on}</div>
        </div>
      ))}
    </>
  )
}
