import { useEffect, useState } from 'react'
import { api } from '../api'
import CaseExplorer, { CaseRow } from '../components/CaseExplorer'
import FilterBar, { EMPTY_FILTERS, FilterState, toQuery } from '../components/FilterBar'
import ReferenceEditor from '../components/ReferenceEditor'
import VariantExplorer, { DfgEdge, Variant } from '../components/VariantExplorer'

interface Summary {
  process: string
  cases: number
  events: number
  variants: number
  median_days: number
  p90_days: number
  rework_share: number
  conforming_share: number
  reference_variant: string
  reference_note: string
  filters_applied: string[]
}
interface Bottleneck {
  transition: string
  mean_hours: number
  count: number
}
interface Insight {
  title: string
  detail: string
  severity: 'info' | 'warn'
  based_on: string
}
import KpiBuilder, { KpiViz, CustomKpi } from '../components/KpiBuilder'

export default function DashboardPage() {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [variants, setVariants] = useState<Variant[]>([])
  const [dfgEdges, setDfgEdges] = useState<DfgEdge[]>([])
  const [bottlenecksData, setBottlenecks] = useState<Bottleneck[]>([])
  const [insights, setInsights] = useState<Insight[]>([])
  const [cases, setCases] = useState<CaseRow[]>([])
  const [customKpis, setCustomKpis] = useState<CustomKpi[]>([])
  const [error, setError] = useState('')
  const [refreshTick, setRefreshTick] = useState(0)
  const [showBuilder, setShowBuilder] = useState(false)

  const q = toQuery(filters)

  useEffect(() => {
    const bump = () => setRefreshTick((t) => t + 1)
    window.addEventListener('piq:kpis-changed', bump)
    return () => window.removeEventListener('piq:kpis-changed', bump)
  }, [])

  useEffect(() => {
    setError('')
    Promise.all([
      api<Summary>(`/api/analysis/summary${q}`),
      api<Variant[]>(`/api/analysis/variants${q}`),
      api<{ edges: DfgEdge[] }>(`/api/analysis/dfg${q}`),
      api<{ edges: Bottleneck[] }>(`/api/analysis/bottlenecks${q}`),
      api<{ insights: Insight[] }>(`/api/analysis/insights${q}`),
      api<{ cases: CaseRow[] }>(`/api/analysis/cases${q}`),
      api<CustomKpi[]>(`/api/kpis${q}`),
    ])
      .then(([s, v, g, b, i, c, k]) => {
        setSummary(s)
        setVariants(v)
        setDfgEdges(g.edges)
        setBottlenecks(b.edges)
        setInsights(i.insights)
        setCases(c.cases)
        setCustomKpis(k)
      })
      .catch((e) => setError(e.message))
  }, [q, refreshTick])

  async function removeKpi(id: string) {
    try {
      await api(`/api/kpis/${id}`, { method: 'DELETE' })
    } catch (e: any) {
      if (e.status !== 404) setError(`Could not remove KPI: ${e.message}`)
    } finally {
      setRefreshTick((t) => t + 1)
    }
  }

  if (error && !summary) {
    return (
      <>
        <h1>Dashboard</h1>
        <div className="error-banner">{error}</div>
      </>
    )
  }
  if (!summary) return <h1>Loading…</h1>

  return (
    <>
      <h1>Process dashboard</h1>
      <p className="lead">
        {summary.process.charAt(0).toUpperCase() + summary.process.slice(1)},{' '}
        {summary.cases} cases / {summary.events} events
        {summary.filters_applied.length > 0 ? ' (filtered)' : ''}.
      </p>

      <FilterBar value={filters} onChange={setFilters} />
      {error && <div className="error-banner">{error}</div>}
      {summary.filters_applied.length > 0 && (
        <div className="row" style={{ marginBottom: 14 }}>
          {summary.filters_applied.map((f) => (
            <span className="chip on" key={f}>
              {f}
            </span>
          ))}
        </div>
      )}

      <div className="grid kpis" style={{ marginBottom: 18 }}>
        <div className="kpi">
          <div className="value">{summary.median_days.toFixed(1)}d</div>
          <div className="label">Median cycle time (p90 {summary.p90_days.toFixed(1)}d)</div>
        </div>
        <div className="kpi" title={summary.reference_note}>
          <div className="value">{(summary.conforming_share * 100).toFixed(0)}%</div>
          <div className="label">Conformant cases</div>
        </div>
        <div className="kpi">
          <div className="value">{(summary.rework_share * 100).toFixed(0)}%</div>
          <div className="label">Cases with rework</div>
        </div>
        <div className="kpi">
          <div className="value">{summary.variants}</div>
          <div className="label">Distinct process variants</div>
        </div>
        {customKpis.filter((k) => !k.series).map((k) => (
          <div className="kpi custom" key={k.id} title="Custom KPI">
            <button className="kpi-remove" onClick={() => removeKpi(k.id)} title="Remove KPI">
              ✕
            </button>
            <div className="value">
              {k.value ?? '—'}
              <span style={{ fontSize: 14, fontWeight: 600 }}> {k.unit}</span>
            </div>
            <div className="label">{k.name}</div>
          </div>
        ))}
      </div>

      <div className="row" style={{ marginTop: -8, marginBottom: 14 }}>
        <button className="primary" onClick={() => setShowBuilder(!showBuilder)}>
          New KPI
        </button>
        <span className="hint">
          Configure a metric and its representation — or ask the co-pilot in plain
          English; both land here and respect your filters.
        </span>
      </div>

      {showBuilder && (
        <KpiBuilder onSaved={() => setRefreshTick((t) => t + 1)}
                    onClose={() => setShowBuilder(false)} />
      )}

      {customKpis.some((k) => k.series) && (
        <div className="grid" style={{ gridTemplateColumns:
             'repeat(auto-fill, minmax(380px, 1fr))', gap: 14, marginBottom: 18 }}>
          {customKpis.filter((k) => k.series).map((k) => (
            <div className="card" key={k.id} style={{ position: 'relative', margin: 0 }}>
              <button className="kpi-remove" onClick={() => removeKpi(k.id)}
                      title="Remove KPI">✕</button>
              <strong>{k.name}</strong>
              <KpiViz kpi={k} />
            </div>
          ))}
        </div>
      )}

      {insights.length > 0 && (
        <div className="card">
          <h3>Insights</h3>
          {insights.map((i) => (
            <div className={'insight ' + i.severity} key={i.title}>
              <strong>{i.title}.</strong> {i.detail}{' '}
              <span className="hint">Based on: {i.based_on}.</span>
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <h3>Process &amp; variant explorer</h3>
        <VariantExplorer variants={variants} dfgEdges={dfgEdges} />
      </div>

      <div className="card">
        <h3>Case explorer ({cases.length} cases)</h3>
        <CaseExplorer cases={cases} />
      </div>

      <div className="card">
        <h3>Reference process (conformance target)</h3>
        <ReferenceEditor
          allActivities={[...new Set(variants.flatMap((v) => v.activities))].sort()}
          onSaved={() => setRefreshTick((t) => t + 1)}
        />
      </div>

      <div className="card">
        <h3>Bottlenecks — slowest hand-offs</h3>
        <table>
          <thead>
            <tr>
              <th>Transition</th>
              <th>Avg wait</th>
              <th>Occurrences</th>
            </tr>
          </thead>
          <tbody>
            {bottlenecksData.map((b) => (
              <tr key={b.transition}>
                <td>{b.transition}</td>
                <td>{(b.mean_hours / 24).toFixed(1)} days</td>
                <td>{b.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
