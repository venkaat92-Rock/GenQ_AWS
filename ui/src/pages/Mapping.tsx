import { useEffect, useState } from 'react'
import { api } from '../api'

interface Activity {
  name: string
  source_table: string
  condition: string
  timestamp: string
  rationale: string
  confidence: 'high' | 'medium' | 'low'
  sql: string
  sql_explanation?: string
  sql_edited_by?: string | null
}
interface Question {
  id: string
  question: string
  why_it_matters: string
  options: string[]
  answer: string | null
}
interface Spec {
  process: string
  status: string
  version: number
  case_id: { source: string; description: string }
  activities: Activity[]
  attributes: { name: string; expression: string; rationale: string }[]
  open_questions: Question[]
  reviewer: string | null
  notes: string
}

// Review order: the choices most likely to be wrong come first (§1.1)
const CONFIDENCE_ORDER = { low: 0, medium: 1, high: 2 }

export default function MappingPage(props: { onMaterialized: () => void }) {
  const [spec, setSpec] = useState<Spec | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [pendingAnswers, setPendingAnswers] = useState<Record<string, string>>({})
  const [reviewer, setReviewer] = useState('')
  const [materialized, setMaterialized] = useState<{ events: number; cases: number } | null>(null)
  const [editingSql, setEditingSql] = useState<string | null>(null) // activity name
  const [sqlDraft, setSqlDraft] = useState('')
  const [sqlEditor, setSqlEditor] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    api<Spec>('/api/mapping')
      .then(setSpec)
      .catch((e) => {
        if (e.status !== 404) setError(e.message)
      })
  }, [])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await action()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const draft = () =>
    run(async () => {
      setSpec(await api<Spec>('/api/mapping/draft', { method: 'POST' }))
      setMaterialized(null)
    })

  const submitAnswers = () =>
    run(async () => {
      setSpec(
        await api<Spec>('/api/mapping/answers', {
          method: 'POST',
          body: JSON.stringify({ answers: pendingAnswers }),
        }),
      )
      setPendingAnswers({})
    })

  const saveSql = (activity: string) =>
    run(async () => {
      const result = await api<Spec & { edit_note: string }>('/api/mapping/sql', {
        method: 'PUT',
        body: JSON.stringify({ activity, sql: sqlDraft, updated_by: sqlEditor }),
      })
      setSpec(result)
      setEditingSql(null)
      setNotice(result.edit_note)
      setMaterialized(null)
    })

  const confirm = () =>
    run(async () => {
      const result = await api<{ materialized: { events: number; cases: number } }>(
        '/api/mapping/confirm',
        { method: 'POST', body: JSON.stringify({ reviewer }) },
      )
      setMaterialized(result.materialized)
      setSpec(await api<Spec>('/api/mapping'))
    })

  // unlock a confirmed mapping into a new editable version (keeps content, bumps version)
  const reopen = () =>
    run(async () => {
      const s = await api<Spec>('/api/mapping/reopen', { method: 'POST' })
      setSpec(s)
      setMaterialized(null)
      setReviewer('')
      setNotice(`Version ${s.version} opened for editing — the previous version stays ` +
        `live until you confirm this one.`)
    })

  if (!spec) {
    return (
      <>
        <h1>Mapping review</h1>
        <p className="lead">
          The agent reads your staged tables and proposes how to turn them into an event log:
          what a case is, which rows mean which business activity, and which timestamp to
          trust. You review its reasoning once — then everything downstream is self-serve.
        </p>
        {error && <div className="error-banner">{error}</div>}
        <button className="primary" onClick={draft} disabled={busy}>
          {busy ? 'Reading your schema…' : 'Draft the mapping'}
        </button>
      </>
    )
  }

  const unanswered = spec.open_questions.filter((q) => !q.answer)
  const confirmed = spec.status === 'confirmed'
  const activities = [...spec.activities].sort(
    (a, b) => CONFIDENCE_ORDER[a.confidence] - CONFIDENCE_ORDER[b.confidence],
  )
  const allAnswered = unanswered.every((q) => pendingAnswers[q.id])

  return (
    <>
      <h1>
        Mapping review{' '}
        <span className="badge status">
          v{spec.version} · {spec.status}
        </span>
      </h1>
      <p className="lead">
        Every choice below is the agent's proposal, with its reasoning. Amber and red items are
        the ones the agent itself is least sure about — review those first. Nothing runs until
        you confirm.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}
      {materialized && (
        <div className="success-banner">
          Event log materialized: {materialized.events} events across {materialized.cases}{' '}
          cases.{' '}
          <a href="#" onClick={props.onMaterialized}>
            Open the dashboard
          </a>
        </div>
      )}

      {unanswered.length > 0 && (
        <div className="card">
          <h3>Decisions required ({unanswered.length})</h3>
          {unanswered.map((q) => (
            <div className="question" key={q.id}>
              <div className="q">{q.question}</div>
              <div className="why">{q.why_it_matters}</div>
              {q.options.map((option) => (
                <label key={option}>
                  <input
                    type="radio"
                    name={q.id}
                    checked={pendingAnswers[q.id] === option}
                    onChange={() => setPendingAnswers({ ...pendingAnswers, [q.id]: option })}
                  />
                  {option}
                </label>
              ))}
            </div>
          ))}
          <button className="primary" onClick={submitAnswers} disabled={busy || !allAnswered}>
            Submit answers
          </button>
        </div>
      )}

      <div className="card">
        <h3>Case = {spec.case_id.source}</h3>
        <p className="hint">{spec.case_id.description}</p>
      </div>

      <h3 style={{ margin: '18px 0 10px' }}>Activities ({activities.length})</h3>
      {activities.map((a) => (
        <div className={'activity ' + a.confidence} key={a.name}>
          <div className="head">
            <span className="name">{a.name}</span>
            <span className={'badge ' + a.confidence}>{a.confidence} confidence</span>
          </div>
          <div className="meta">
            {a.source_table} · {a.condition} · timestamp: {a.timestamp}
          </div>
          <div className="rationale">{a.rationale}</div>
          <details className="sql">
            <summary>Transformation detail</summary>
            {a.sql_explanation && (
              <dl className="sql-explain">
                {a.sql_explanation.split(' | ').map((part, i) => {
                  const idx = part.indexOf(':')
                  const label = idx > 0 ? part.slice(0, idx) : 'Definition'
                  const text = idx > 0 ? part.slice(idx + 1).trim() : part
                  return (
                    <div className="sql-explain-row" key={i}>
                      <dt>{label}</dt>
                      <dd>{text}</dd>
                    </div>
                  )
                })}
              </dl>
            )}
            {a.sql_edited_by && (
              <p className="hint">SQL overridden by {a.sql_edited_by}; the override takes precedence over the agent's draft.</p>
            )}
            {editingSql === a.name ? (
              <>
                <textarea
                  className="sql-editor"
                  rows={5}
                  value={sqlDraft}
                  onChange={(e) => setSqlDraft(e.target.value)}
                  spellCheck={false}
                />
                <div className="row" style={{ marginTop: 8 }}>
                  <input
                    type="text"
                    placeholder="Your name (SME sign-off)"
                    style={{ maxWidth: 200 }}
                    value={sqlEditor}
                    onChange={(e) => setSqlEditor(e.target.value)}
                  />
                  <button
                    className="primary"
                    onClick={() => saveSql(a.name)}
                    disabled={busy || !sqlDraft.trim() || !sqlEditor.trim()}
                  >
                    Validate &amp; save SQL
                  </button>
                  <button className="ghost" onClick={() => setEditingSql(null)}>
                    Cancel
                  </button>
                </div>
                <p className="hint" style={{ marginTop: 6 }}>
                  Must return the columns <code className="inline">case_id</code> and{' '}
                  <code className="inline">timestamp</code>. It is test-run against your staged
                  tables before saving, and saving puts the mapping back into draft for
                  re-confirmation.
                </p>
              </>
            ) : (
              <>
                <pre>{a.sql}</pre>
                <button
                  className="linklike"
                  onClick={() => {
                    setEditingSql(a.name)
                    setSqlDraft(a.sql)
                  }}
                >
                  Edit SQL
                </button>
              </>
            )}
          </details>
        </div>
      ))}

      {spec.attributes.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>Case attributes</h3>
          {spec.attributes.map((attr) => (
            <p key={attr.name} style={{ fontSize: 13.5, margin: '6px 0' }}>
              <code className="inline">{attr.name}</code> — {attr.expression}.{' '}
              <span className="hint">{attr.rationale}</span>
            </p>
          ))}
        </div>
      )}

      {!confirmed && (
        <div className="card">
          <h3>Confirm this mapping</h3>
          <p className="hint" style={{ marginBottom: 10 }}>
            {unanswered.length > 0
              ? 'Blocked until the open questions above are answered.'
              : 'Sign off with your name — this is recorded on the mapping for audit.'}
          </p>
          <div className="row">
            <input
              type="text"
              placeholder="Reviewer name"
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
            />
            <button
              className="primary"
              onClick={confirm}
              disabled={busy || unanswered.length > 0 || !reviewer.trim()}
            >
              Confirm and build event log
            </button>
            <button className="ghost" onClick={draft} disabled={busy}>
              Re-draft
            </button>
          </div>
        </div>
      )}

      {confirmed && (
        <div className="success-banner">
          <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap',
                                         gap: 10 }}>
            <span>
              Confirmed by {spec.reviewer} · version {spec.version}. Dashboards run on this
              mapping. Start a new version to change it — the current one stays live until
              you re-confirm.
            </span>
            <div className="row">
              <button className="primary" onClick={reopen} disabled={busy}>
                Start a new version
              </button>
              <button className="ghost" onClick={draft} disabled={busy}>
                Re-draft from agent
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
