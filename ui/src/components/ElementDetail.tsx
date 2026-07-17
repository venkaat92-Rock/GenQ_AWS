import { useEffect, useState } from 'react'
import { api } from '../api'

interface Run {
  at: string
  trigger: string
  status?: string
  events?: number
  cases?: number
  error?: string
  reason?: string
}
interface Schedule {
  enabled: boolean
  frequency: string
  updated_by: string | null
  next_run: string | null
}
interface DfgNode {
  activity: string
  event_count: number
}

// Why each element exists — grounded in how Celonis Data Integration is organized.
const PURPOSE: Record<string, string> = {
  connections:
    'Where data enters the system. In Celonis these are live source connections; here they ' +
    'are your staged CSV/table uploads (live SAP connectors arrive in Phase 4).',
  schedule:
    'Schedules re-run the data load on a cadence so dashboards stay current without anyone ' +
    'clicking Run. A schedule only ever executes a mapping a human has confirmed.',
  data_jobs:
    'A data job is the ordered set of transformation tasks that turns raw tables into the ' +
    'event log. In Celonis, consultants write these by hand (268 in your AP Accelerator); ' +
    'here the AI agent generates them and you review them in Mapping review.',
  storage:
    'The processed data at rest. ProcessIQ uses an embedded DuckDB warehouse per workspace — ' +
    'staged source tables plus the materialized event log the dashboards query.',
  objects_events:
    'What the load produced: objects (cases — here, purchase orders) and the events on them. ' +
    'This is the data every dashboard, filter, and co-pilot answer is computed from.',
}

export default function ElementDetail(props: {
  element: string
  state: any
  onRun: () => void
  running: boolean
  onEditModel: () => void
  onScheduleSaved: () => void
}) {
  const { element, state } = props
  const [tab, setTab] = useState<'tasks' | 'logs'>('tasks')
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [schedule, setSchedule] = useState<Schedule | null>(null)
  const [schedDraft, setSchedDraft] = useState<{ enabled: boolean; frequency: string } | null>(null)
  const [reviewer, setReviewer] = useState('')
  const [nodes, setNodes] = useState<DfgNode[] | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    setError('')
    setNotice('')
    if (element === 'data_jobs') {
      api<{ runs: Run[] }>('/api/dataload/runs').then((r) => setRuns(r.runs)).catch(() => {})
    }
    if (element === 'schedule') {
      api<Schedule>('/api/schedule')
        .then((s) => {
          setSchedule(s)
          setSchedDraft({ enabled: s.enabled, frequency: s.frequency })
        })
        .catch((e) => setError(e.message))
    }
    if (element === 'objects_events' && state.output.events > 0) {
      api<{ nodes: DfgNode[] }>('/api/analysis/dfg')
        .then((g) => setNodes(g.nodes))
        .catch(() => {})
    }
  }, [element])

  async function saveSchedule() {
    if (!schedDraft) return
    setError('')
    try {
      const saved = await api<Schedule>('/api/schedule', {
        method: 'PUT',
        body: JSON.stringify({ ...schedDraft, updated_by: reviewer }),
      })
      setSchedule(saved)
      setNotice(
        saved.enabled
          ? `Schedule active — next load ${saved.next_run?.slice(0, 16).replace('T', ' ')} UTC.`
          : 'Schedule disabled.',
      )
      props.onScheduleSaved()
    } catch (e: any) {
      setError(e.message)
    }
  }

  const title: Record<string, string> = {
    connections: 'Data connections',
    schedule: 'Schedules',
    data_jobs: 'Data jobs',
    storage: 'Data storage',
    objects_events: 'Objects and events',
    data_model: 'Data model',
  }

  if (element === 'data_model') return null // handled by the dedicated View data model card

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3>{title[element] ?? element}</h3>
      <p className="hint" style={{ maxWidth: 640, marginBottom: 12 }}>{PURPOSE[element]}</p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      {element === 'connections' && (
        <table>
          <thead>
            <tr><th>Connection</th><th>Kind</th><th>Tables</th><th>Status</th></tr>
          </thead>
          <tbody>
            {state.sources.connections.map((c: any) => (
              <tr key={c.name}>
                <td>{c.name}</td>
                <td>{c.kind}</td>
                <td>{c.tables}</td>
                <td><span className={'badge ' + (c.status === 'ok' ? 'high' : 'medium')}>{c.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {element === 'schedule' && schedule && schedDraft && (
        <>
          <div className="row" style={{ gap: 14 }}>
            <label className="row" style={{ gap: 6, fontSize: 14 }}>
              <input
                type="checkbox"
                checked={schedDraft.enabled}
                onChange={(e) => setSchedDraft({ ...schedDraft, enabled: e.target.checked })}
              />
              Enabled
            </label>
            <select
              value={schedDraft.frequency}
              onChange={(e) => setSchedDraft({ ...schedDraft, frequency: e.target.value })}
            >
              <option value="hourly">Every hour</option>
              <option value="daily">Every day</option>
            </select>
            <input
              type="text"
              placeholder="Your name (SME sign-off)"
              style={{ maxWidth: 200 }}
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
            />
            <button className="primary" onClick={saveSchedule} disabled={!reviewer.trim()}>
              Save schedule
            </button>
          </div>
          <p className="hint" style={{ marginTop: 10 }}>
            {schedule.enabled
              ? `Currently: ${schedule.frequency}, next load ${schedule.next_run?.slice(0, 16).replace('T', ' ')} UTC, set by ${schedule.updated_by}.`
              : 'Currently: no schedule — loads run on confirm or Run now.'}
            {' '}Enabling requires a confirmed data model.
          </p>
        </>
      )}

      {element === 'data_jobs' && (
        <>
          <div className="row" style={{ marginBottom: 10 }}>
            <button className={'mode-btn' + (tab === 'tasks' ? ' on' : '')} onClick={() => setTab('tasks')}>
              Tasks ({state.processing.jobs.transformations})
            </button>
            <button className={'mode-btn' + (tab === 'logs' ? ' on' : '')} onClick={() => setTab('logs')}>
              Logs ({runs?.length ?? '…'})
            </button>
            <button className="ghost" style={{ marginLeft: 'auto' }} onClick={props.onRun} disabled={props.running}>
              Execute data job
            </button>
            <button className="ghost" onClick={props.onEditModel}>Edit in mapping review</button>
          </div>
          {tab === 'tasks' && (
            <table>
              <thead>
                <tr><th>Task</th><th>Type</th><th>Source</th><th>Confidence</th></tr>
              </thead>
              <tbody>
                {state.processing.jobs.transformation_list.map((t: any) => (
                  <tr key={t.kind + t.name}>
                    <td>{t.kind === 'activity' ? `Add Activity: ${t.name}` : `Add Attribute: ${t.name}`}</td>
                    <td>{t.kind}</td>
                    <td className="hint">{t.source}</td>
                    <td><span className={'badge ' + t.confidence}>{t.confidence}</span></td>
                  </tr>
                ))}
                {state.processing.jobs.transformation_list.length === 0 && (
                  <tr><td colSpan={4} className="hint">No transformations yet — draft a mapping first.</td></tr>
                )}
              </tbody>
            </table>
          )}
          {tab === 'logs' && (
            <table>
              <thead>
                <tr><th>When (UTC)</th><th>Trigger</th><th>Result</th></tr>
              </thead>
              <tbody>
                {(runs ?? []).map((r, i) => (
                  <tr key={i}>
                    <td>{r.at.slice(0, 19).replace('T', ' ')}</td>
                    <td>{r.trigger}</td>
                    <td>
                      {r.status === 'failed' ? (
                        <span className="badge low">failed: {r.error}</span>
                      ) : r.status === 'skipped' ? (
                        <span className="badge medium">skipped — {r.reason}</span>
                      ) : (
                        <span className="badge high">{r.events} events / {r.cases} cases</span>
                      )}
                    </td>
                  </tr>
                ))}
                {(runs ?? []).length === 0 && (
                  <tr><td colSpan={3} className="hint">No loads recorded yet.</td></tr>
                )}
              </tbody>
            </table>
          )}
        </>
      )}

      {element === 'storage' && (
        <>
          <p style={{ fontSize: 14, marginBottom: 10 }}>
            {state.processing.storage.engine} · {state.processing.storage.size_mb} MB on disk
          </p>
          <table>
            <thead>
              <tr><th>Table</th><th>Kind</th><th>Rows</th></tr>
            </thead>
            <tbody>
              {Object.entries(state.processing.storage.tables).map(([name, rows]: any) => (
                <tr key={name}>
                  <td><code className="inline">{name}</code></td>
                  <td>staged source</td>
                  <td>{rows}</td>
                </tr>
              ))}
              {state.output.events > 0 && (
                <tr>
                  <td><code className="inline">EVENT_LOG</code></td>
                  <td>materialized</td>
                  <td>{state.output.events}</td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}

      {element === 'objects_events' && (
        <>
          <div className="row" style={{ gap: 24, marginBottom: 12 }}>
            <div><strong style={{ fontSize: 22 }}>{state.output.objects}</strong> <span className="hint">objects (cases)</span></div>
            <div><strong style={{ fontSize: 22 }}>{state.output.events}</strong> <span className="hint">events</span></div>
          </div>
          {nodes && (
            <table>
              <thead>
                <tr><th>Event type (activity)</th><th>Occurrences</th></tr>
              </thead>
              <tbody>
                {nodes.map((n) => (
                  <tr key={n.activity}>
                    <td>{n.activity}</td>
                    <td>{n.event_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {state.output.events === 0 && (
            <p className="hint">Nothing materialized yet — confirm a mapping first.</p>
          )}
        </>
      )}
    </div>
  )
}
