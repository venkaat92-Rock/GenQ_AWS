import { useState } from 'react'
import { api } from '../api'

export interface CaseRow {
  case_id: string
  start: string
  end: string
  events: number
  duration_days: number
  variant: string
  rework: boolean
  conformant?: boolean
  vendor?: string
  customer?: string
  net_value?: number
}

type SortKey = 'duration_days' | 'start' | 'net_value' | 'case_id'

export default function CaseExplorer(props: { cases: CaseRow[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('duration_days')
  const [desc, setDesc] = useState(true)
  const [openCase, setOpenCase] = useState<string | null>(null)
  const [timeline, setTimeline] = useState<{ activity: string; timestamp: string }[]>([])

  const sorted = [...props.cases].sort((a, b) => {
    const av = a[sortKey] ?? 0
    const bv = b[sortKey] ?? 0
    const cmp = av < bv ? -1 : av > bv ? 1 : 0
    return desc ? -cmp : cmp
  })

  const sortBy = (key: SortKey) => {
    if (key === sortKey) setDesc(!desc)
    else {
      setSortKey(key)
      setDesc(true)
    }
  }

  async function toggleCase(id: string) {
    if (openCase === id) {
      setOpenCase(null)
      return
    }
    const detail = await api<{ events: { activity: string; timestamp: string }[] }>(
      `/api/analysis/case/${encodeURIComponent(id)}`,
    )
    setTimeline(detail.events)
    setOpenCase(id)
  }

  const arrow = (key: SortKey) => (sortKey === key ? (desc ? ' ↓' : ' ↑') : '')
  const partyLabel = props.cases.some((c) => c.customer != null) ? 'Customer' : 'Vendor'

  return (
    <div style={{ overflowX: 'auto' }}>
      <table>
        <thead>
          <tr>
            <th className="sortable" onClick={() => sortBy('case_id')}>Case{arrow('case_id')}</th>
            <th>{partyLabel}</th>
            <th className="sortable" onClick={() => sortBy('net_value')}>Value{arrow('net_value')}</th>
            <th className="sortable" onClick={() => sortBy('start')}>Started{arrow('start')}</th>
            <th className="sortable" onClick={() => sortBy('duration_days')}>
              Duration{arrow('duration_days')}
            </th>
            <th>Flags</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => (
            <>
              <tr
                key={c.case_id}
                className="case-row"
                onClick={() => toggleCase(c.case_id)}
                title={c.variant}
              >
                <td>
                  <code className="inline">{c.case_id}</code>
                </td>
                <td>{c.customer ?? c.vendor ?? '—'}</td>
                <td>{c.net_value != null ? c.net_value.toLocaleString() : '—'}</td>
                <td>{c.start.slice(0, 10)}</td>
                <td>{c.duration_days.toFixed(1)}d</td>
                <td>
                  {c.rework && <span className="badge medium">rework</span>}{' '}
                  {c.conformant === false && <span className="badge low">deviates</span>}
                  {c.conformant && <span className="badge high">on path</span>}
                </td>
              </tr>
              {openCase === c.case_id && (
                <tr key={c.case_id + '-detail'}>
                  <td colSpan={6}>
                    <div className="timeline">
                      {timeline.map((e, i) => (
                        <div className="timeline-step" key={i}>
                          <span className="timeline-dot" />
                          <strong>{e.activity}</strong>
                          <span className="hint">{e.timestamp.slice(0, 16).replace('T', ' ')}</span>
                        </div>
                      ))}
                    </div>
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
      <p className="hint" style={{ marginTop: 8 }}>
        Click a case to see its event timeline. Click column headers to sort.
      </p>
    </div>
  )
}
