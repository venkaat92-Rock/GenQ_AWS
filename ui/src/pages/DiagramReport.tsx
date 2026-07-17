import { useEffect, useState } from 'react'
import { api } from '../api'

interface Row {
  id: string
  name: string
  level: string
  status: string
  owner: string
  approver: string | null
  versions: number
  updated_at: string | null
  approved_by?: string | null
  approved_at?: string | null
}
interface Activity {
  action: string
  by: string
  at: string
  comment: string
  model: string
  model_id: string
}
interface Report {
  total: number
  total_versions: number
  by_level: Record<string, number>
  by_status: Record<string, number>
  by_owner: { owner: string; count: number }[]
  by_month: { month: string; count: number }[]
  approval_rate: number
  pending_approvals: Row[]
  recently_approved: Row[]
  recent_activity: Activity[]
}

const STATUS_BADGE: Record<string, string> = {
  draft: 'status', in_review: 'medium', approved: 'high', rejected: 'low',
}
const LEVEL_HINT: Record<string, string> = {
  L1: 'Landscape', L2: 'Group', L3: 'End-to-end', L4: 'Detailed',
}
const when = (s: string | null | undefined) =>
  s ? s.replace('T', ' ').replace('+00:00', ' UTC').slice(0, 19) : '—'

export default function DiagramReportPage() {
  const [rep, setRep] = useState<Report | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api<Report>('/api/diagram/repo/report').then(setRep).catch((e) => setError(e.message))
  }, [])

  const exportCsv = () => {
    if (!rep) return
    const rows = [['Model', 'Level', 'Status', 'Owner', 'Approver', 'Versions',
                   'Updated']]
    const all = [...rep.pending_approvals, ...rep.recently_approved]
    for (const r of all) {
      rows.push([r.name, r.level, r.status, r.owner, r.approver ?? '',
                 String(r.versions), when(r.updated_at)])
    }
    const csv = rows.map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(',')).join('\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    a.download = 'model_report.csv'
    a.click()
    URL.revokeObjectURL(a.href)
  }

  if (error) return (<><h1>Model report</h1><div className="error-banner">{error}</div></>)
  if (!rep) return <h1>Loading…</h1>

  const s = rep.by_status
  const maxMonth = Math.max(...rep.by_month.map((m) => m.count), 1)

  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1>Model report</h1>
          <p className="lead">
            Governance at a glance — how many process models exist, where they sit in the
            approval workflow, and recent activity. The DiagramIQ equivalent of Signavio
            Model Reporting.
          </p>
        </div>
        <button onClick={exportCsv}>Export CSV</button>
      </div>

      <div className="grid kpis" style={{ marginBottom: 18 }}>
        <div className="kpi"><div className="value">{rep.total}</div>
          <div className="label">Models</div></div>
        <div className="kpi"><div className="value">{s.approved ?? 0}
          <span style={{ fontSize: 14, fontWeight: 600 }}> ({rep.approval_rate}%)</span></div>
          <div className="label">Approved</div></div>
        <div className="kpi"><div className="value">{s.in_review ?? 0}</div>
          <div className="label">Awaiting approval</div></div>
        <div className="kpi"><div className="value">{s.draft ?? 0}</div>
          <div className="label">Draft</div></div>
        <div className="kpi"><div className="value">{s.rejected ?? 0}</div>
          <div className="label">Rejected</div></div>
        <div className="kpi"><div className="value">{rep.total_versions}</div>
          <div className="label">Total revisions</div></div>
      </div>

      <div className="row" style={{ gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div className="card" style={{ flex: 1, minWidth: 280 }}>
          <h3>By level</h3>
          <table>
            <tbody>
              {(['L1', 'L2', 'L3', 'L4'] as const).map((l) => (
                <tr key={l}>
                  <td><span className="badge status">{l}</span>{' '}
                    <span className="hint">{LEVEL_HINT[l]}</span></td>
                  <td style={{ textAlign: 'right' }}>{rep.by_level[l] ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card" style={{ flex: 1, minWidth: 280 }}>
          <h3>By owner</h3>
          {rep.by_owner.length === 0 ? <p className="hint">No models yet.</p> : (
            <table>
              <tbody>
                {rep.by_owner.map((o) => (
                  <tr key={o.owner}><td>{o.owner}</td>
                    <td style={{ textAlign: 'right' }}>{o.count}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {rep.by_month.length > 0 && (
          <div className="card" style={{ flex: 1, minWidth: 280 }}>
            <h3>Created over time</h3>
            {rep.by_month.map((m) => (
              <div key={m.month} className="row" style={{ gap: 8, marginBottom: 4 }}>
                <span className="hint" style={{ width: 64 }}>{m.month}</span>
                <div style={{ flex: 1, background: '#eef1f8', borderRadius: 4, height: 14 }}>
                  <div style={{ width: `${(m.count / maxMonth) * 100}%`, height: '100%',
                                background: 'var(--accent)', borderRadius: 4 }} />
                </div>
                <span style={{ width: 24, fontWeight: 650, fontSize: 12 }}>{m.count}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <h3>Awaiting approval ({rep.pending_approvals.length})</h3>
        {rep.pending_approvals.length === 0 ? (
          <p className="hint">Nothing is in review right now.</p>
        ) : (
          <table>
            <thead><tr><th>Model</th><th>Level</th><th>Owner</th><th>Approver</th>
              <th>Rev.</th><th>Submitted</th></tr></thead>
            <tbody>
              {rep.pending_approvals.map((r) => (
                <tr key={r.id}>
                  <td>{r.name}</td>
                  <td><span className="badge status">{r.level}</span></td>
                  <td className="hint">{r.owner}</td>
                  <td>{r.approver ?? '—'}</td>
                  <td>v{r.versions}</td>
                  <td className="hint">{when(r.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3>Recently approved ({rep.recently_approved.length})</h3>
        {rep.recently_approved.length === 0 ? (
          <p className="hint">No approved models yet.</p>
        ) : (
          <table>
            <thead><tr><th>Model</th><th>Level</th><th>Approved by</th>
              <th>Approved</th><th>Rev.</th></tr></thead>
            <tbody>
              {rep.recently_approved.map((r) => (
                <tr key={r.id}>
                  <td>{r.name}</td>
                  <td><span className="badge status">{r.level}</span></td>
                  <td>{r.approved_by ?? '—'}</td>
                  <td className="hint">{when(r.approved_at)}</td>
                  <td>v{r.versions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3>Recent activity</h3>
        {rep.recent_activity.length === 0 ? (
          <p className="hint">No activity yet.</p>
        ) : (
          <table>
            <thead><tr><th>When</th><th>Action</th><th>Model</th><th>By</th>
              <th>Comment</th></tr></thead>
            <tbody>
              {rep.recent_activity.map((a, i) => (
                <tr key={i}>
                  <td className="hint">{when(a.at)}</td>
                  <td><span className={'badge ' + (STATUS_BADGE[a.action] ?? 'status')}>
                    {a.action.replace('_', ' ')}</span></td>
                  <td>{a.model}</td>
                  <td>{a.by}</td>
                  <td className="hint">{a.comment}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
