import { useEffect, useState } from 'react'
import { api } from '../api'
import DataModelView from '../components/DataModelView'
import ElementDetail from '../components/ElementDetail'

interface Transformation {
  name: string
  kind: 'activity' | 'attribute'
  source: string
  confidence: string
}
interface DataLoadState {
  sources: {
    connections: { name: string; kind: string; tables: number; status: string }[]
    note: string
  }
  processing: {
    schedule: { mode: string; note: string }
    jobs: {
      extractions: number
      transformations: number
      transformation_list: Transformation[]
      model_loads: number
      status: string
    }
    storage: { engine: string; size_mb: number; tables: Record<string, number> }
    last_run: { at: string; trigger: string; events?: number } | null
  }
  output: {
    objects: number
    events: number
    data_model: {
      name: string | null
      status: string
      version: number | null
      confirmed_by: string | null
      activities: number
      attributes: number
      open_questions: number
      authored_by: string | null
    }
  }
  consumers: string[]
}

interface SpecActivity {
  name: string
  source_table: string
  condition: string
  timestamp: string
  rationale: string
  confidence: string
  sql: string
}

interface BedrockModel {
  id: string
  label: string
  note: string
}

interface AiSettings {
  provider: 'anthropic' | 'vertex' | 'bedrock'
  model: string
  has_anthropic_key: boolean
  has_server_key: boolean
  vertex_project: string
  vertex_region: string
  has_aws_keys: boolean
  aws_region: string
  sdk: Record<string, boolean>
  active: boolean
  source: 'workspace' | 'environment' | 'iam-role' | 'none'
  // curated Bedrock registry served from api/aws_models.py
  bedrock_models?: BedrockModel[]
  bedrock_default?: boolean
  default_aws_region?: string
}

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: 'Anthropic API', vertex: 'Google Vertex AI', bedrock: 'AWS Bedrock',
}

function pickSecrets(d: Record<string, string>) {
  return {
    anthropic_key: d.anthropic_key ?? '',
    aws_access_key: d.aws_access_key ?? '',
    aws_secret_key: d.aws_secret_key ?? '',
  }
}
// Fallbacks only — the Bedrock list is served by the API (api/aws_models.py),
// so curating models never needs a frontend rebuild.
const MODEL_SUGGESTIONS: Record<string, string[]> = {
  anthropic: ['claude-sonnet-5', 'claude-opus-4-8', 'claude-haiku-4-5-20251001'],
  vertex: ['claude-sonnet-5', 'claude-opus-4-8'],
  bedrock: ['us.anthropic.claude-opus-4-5-20251101-v1:0',
            'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
            'us.anthropic.claude-haiku-4-5-20251001-v1:0'],
}

export default function DataLoadPage(props: {
  onSelect: (element: string | null) => void
  selected: string | null
  onEditModel: () => void
}) {
  const [state, setState] = useState<DataLoadState | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [showModel, setShowModel] = useState(false)
  const [spec, setSpec] = useState<{ activities: SpecActivity[]; case_id: { source: string; description: string } } | null>(null)
  const [ai, setAi] = useState<AiSettings | null>(null)
  const [aiDraft, setAiDraft] = useState<Record<string, string>>({})
  const [aiTest, setAiTest] = useState<{ ok: boolean; model: string; error?: string; reply?: string } | null>(null)

  const refresh = () =>
    api<DataLoadState>('/api/dataload').then(setState).catch((e) => setError(e.message))

  const refreshAi = () =>
    api<AiSettings>('/api/ai-provider')
      .then((r) => {
        setAi(r)
        setAiDraft((d) => ({ provider: r.provider, model: r.model,
                             vertex_project: r.vertex_project,
                             vertex_region: r.vertex_region,
                             aws_region: r.aws_region, ...pickSecrets(d) }))
      })
      .catch(() => {})

  useEffect(() => {
    refresh()
    refreshAi()
  }, [])

  // Escape closes the element detail popup
  useEffect(() => {
    if (!props.selected) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') props.onSelect(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [props.selected])

  const saveAi = async () => {
    setBusy(true)
    setError('')
    try {
      const body: Record<string, string> = {
        provider: aiDraft.provider, model: aiDraft.model ?? '',
        vertex_project: aiDraft.vertex_project ?? '',
        vertex_region: aiDraft.vertex_region ?? '',
        aws_region: aiDraft.aws_region ?? '',
      }
      // secrets travel only when typed; stored values stay untouched otherwise
      for (const s of ['anthropic_key', 'aws_access_key', 'aws_secret_key']) {
        if (aiDraft[s]) body[s] = aiDraft[s]
      }
      await api('/api/ai-provider', { method: 'PUT', body: JSON.stringify(body) })
      setAiDraft((d) => ({ ...d, anthropic_key: '', aws_access_key: '',
                           aws_secret_key: '' }))
      await refreshAi()
      setNotice('AI model settings saved.')
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // Invoke the draft model once; surfaces the raw provider/Bedrock error text —
  // IAM-role, model-id and throughput issues become diagnosable on sight.
  const testAi = async () => {
    setBusy(true)
    setAiTest(null)
    try {
      const body: Record<string, string> = {
        provider: aiDraft.provider, model: aiDraft.model ?? '',
        vertex_project: aiDraft.vertex_project ?? '',
        vertex_region: aiDraft.vertex_region ?? '',
        aws_region: aiDraft.aws_region ?? '',
      }
      for (const s of ['anthropic_key', 'aws_access_key', 'aws_secret_key']) {
        if (aiDraft[s]) body[s] = aiDraft[s]
      }
      setAiTest(await api('/api/ai-provider/test',
                          { method: 'POST', body: JSON.stringify(body) }))
    } catch (e: any) {
      setAiTest({ ok: false, model: aiDraft.model ?? '', error: e.message })
    } finally {
      setBusy(false)
    }
  }

  async function runNow() {
    setBusy(true)
    setError('')
    try {
      const r = await api<{ materialized: { events: number; cases: number } }>(
        '/api/dataload/run',
        { method: 'POST' },
      )
      setNotice(
        `Load complete: ${r.materialized.events} events / ${r.materialized.cases} cases rebuilt from staged sources.`,
      )
      await refresh()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function toggleModel() {
    if (!showModel && !spec) {
      try {
        setSpec(await api('/api/mapping'))
      } catch {
        // no mapping drafted yet — the ERD still renders from staged tables
      }
    }
    setShowModel(!showModel)
  }

  if (!state) return <h1>{error ? '' : 'Loading…'}{error && <div className="error-banner">{error}</div>}</h1>

  const { jobs, schedule, storage, last_run } = state.processing
  const model = state.output.data_model
  const sel = (id: string) => (props.selected === id ? ' selected' : '')
  const pick = (id: string) => {
    props.onSelect(props.selected === id ? null : id)
  }
  const ok = (good: boolean) => (
    <span className={'dot ' + (good ? 'good' : 'warn')}>{good ? '✓' : '!'}</span>
  )

  return (
    <>
      <h1>Data load</h1>
      <p className="lead">
        Every attribute below is auto-produced by the AI modelling agent and editable by you —
        no data engineer configured anything. Click any element and ask the co-pilot about it.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      <div className="dl-canvas">
        <div className="dl-lane">
          <div className="dl-lane-title">Your system · Data sources</div>
          {state.sources.connections.map((c) => (
            <button key={c.name} className={'dl-card' + sel('connections')} onClick={() => pick('connections')}>
              <div className="dl-card-head">
                {c.tables} <span>Data Connections</span> {ok(c.status === 'ok')}
              </div>
              <div className="dl-sub">{c.name}</div>
            </button>
          ))}
          <p className="hint" style={{ marginTop: 6 }}>{state.sources.note}</p>
        </div>

        <div className="dl-lane wide">
          <div className="dl-lane-title">ProcessIQ · Data integration <span className="badge status">AI + human</span></div>
          <div className="dl-group">
            <button className={'dl-card' + sel('ai_model')} onClick={() => pick('ai_model')}>
              <div className="dl-card-head">AI Model {ok(!!ai?.active)}</div>
              <div className="dl-sub">
                {ai ? `${PROVIDER_LABEL[ai.provider]} · ${ai.model}` : 'loading…'}
              </div>
              <div className="dl-sub" style={{ color: 'var(--accent)' }}>
                {ai?.source === 'workspace' ? 'workspace credentials'
                  : ai?.source === 'environment' ? 'server key (fallback)'
                  : 'not configured — priors only'}
              </div>
            </button>

            <button className={'dl-card' + sel('schedule')} onClick={() => pick('schedule')}>
              <div className="dl-card-head">
                Schedule {ok(true)}
              </div>
              <div className="dl-sub">{schedule.mode}</div>
            </button>

            <button className={'dl-card' + sel('data_jobs')} onClick={() => pick('data_jobs')}>
              <div className="dl-card-head">
                {jobs.transformations > 0 ? jobs.transformations : '—'} <span>Data Jobs</span>{' '}
                {ok(jobs.status === 'ok')}
              </div>
              <div className="dl-stats">
                <span>
                  <strong>{jobs.extractions}</strong> Extractions
                </span>
                <span>
                  <strong>{jobs.transformations}</strong> Transformations
                </span>
                <span>
                  <strong>{jobs.model_loads}</strong> Model Loads
                </span>
              </div>
              <div className="dl-sub">
                {last_run
                  ? `Last load ${last_run.at.slice(0, 16).replace('T', ' ')} (${last_run.trigger})`
                  : jobs.status}
              </div>
            </button>

            <button className={'dl-card' + sel('storage')} onClick={() => pick('storage')}>
              <div className="dl-card-head">Data Storage {ok(true)}</div>
              <div className="dl-sub">
                {storage.engine} · {storage.size_mb} MB · {Object.keys(storage.tables).length} tables
              </div>
            </button>
            <div className="row" style={{ marginTop: 4 }}>
              <button className="ghost" onClick={runNow} disabled={busy || model.status !== 'working'}>
                {busy ? 'Running…' : 'Run now'}
              </button>
            </div>
          </div>
        </div>

        <div className="dl-lane wide">
          <div className="dl-lane-title">Output</div>
          <button className={'dl-card' + sel('objects_events')} onClick={() => pick('objects_events')}>
            <div className="dl-card-head">Objects and Events {ok(state.output.events > 0)}</div>
            <div className="dl-stats">
              <span>
                <strong>{state.output.objects}</strong> Objects
              </span>
              <span>
                <strong>{state.output.events}</strong> Events
              </span>
            </div>
          </button>

          <button className={'dl-card' + sel('data_model')} onClick={() => pick('data_model')}>
            <div className="dl-card-head">
              Data Model {ok(model.status === 'working')}
            </div>
            <div className="dl-sub">
              {model.name
                ? `${model.name} v${model.version} · ${model.status}`
                : 'not drafted yet'}
            </div>
            {model.authored_by && (
              <div className="dl-sub" style={{ color: 'var(--accent)' }}>
                {model.authored_by}
                {model.confirmed_by ? ` (${model.confirmed_by})` : ''}
              </div>
            )}
          </button>
          <div className="row" style={{ marginTop: 4 }}>
            <button className="ghost" onClick={toggleModel}>
              {showModel ? 'Hide data model' : 'View data model'}
            </button>
            <button className="ghost" onClick={props.onEditModel}>
              Edit in mapping review
            </button>
          </div>
        </div>

        <div className="dl-lane">
          <div className="dl-lane-title">Consumers</div>
          {state.consumers.map((c) => (
            <div key={c} className="dl-card static">
              <div className="dl-card-head">{c}</div>
            </div>
          ))}
        </div>
      </div>

      {props.selected === 'ai_model' && ai && (
        <div className="dl-modal-backdrop" onClick={() => props.onSelect(null)}>
          <div className="dl-modal" onClick={(e) => e.stopPropagation()}>
            <button className="dl-modal-close" onClick={() => props.onSelect(null)}
                    aria-label="Close">✕</button>
          <h3>AI model — curates the mining, never decides it</h3>
          <p className="hint">
            The selected model drafts mappings, narrates transformations, and answers
            the co-pilot. Semantic choices still require your confirmation in Mapping
            review, and generated SQL stays inspectable. Claude runs on whichever
            platform holds your credentials.
          </p>
          <div className="row" style={{ flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <label className="editor-field">Provider
              <select value={aiDraft.provider ?? ai.provider}
                      onChange={(e) => setAiDraft({ ...aiDraft, provider: e.target.value })}>
                {Object.entries(PROVIDER_LABEL).map(([key, label]) => (
                  <option key={key} value={key}
                          disabled={ai.sdk[key] === false}>
                    {label}{ai.sdk[key] === false ? ' (SDK not installed)' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="editor-field">Model
              <input type="text" list="ai-models" style={{ minWidth: 280 }}
                     value={aiDraft.model ?? ai.model}
                     onChange={(e) => setAiDraft({ ...aiDraft, model: e.target.value })} />
              <datalist id="ai-models">
                {((aiDraft.provider ?? ai.provider) === 'bedrock'
                  && ai.bedrock_models?.length
                  ? ai.bedrock_models.map((m) =>
                      ({ id: m.id, hint: [m.label, m.note].filter(Boolean).join(' — ') }))
                  : (MODEL_SUGGESTIONS[aiDraft.provider ?? ai.provider] ?? [])
                      .map((m) => ({ id: m, hint: '' }))
                ).map((m) => (
                  <option key={m.id} value={m.id} label={m.hint || undefined} />
                ))}
              </datalist>
            </label>
          </div>

          {(aiDraft.provider ?? ai.provider) === 'anthropic' && (
            <div className="row" style={{ marginTop: 10, flexWrap: 'wrap' }}>
              <label className="editor-field">API key
                <input type="password" style={{ minWidth: 280 }}
                       placeholder={ai.has_anthropic_key
                         ? 'stored — leave blank to keep' : 'sk-ant-…'}
                       value={aiDraft.anthropic_key ?? ''}
                       onChange={(e) => setAiDraft({ ...aiDraft, anthropic_key: e.target.value })} />
              </label>
            </div>
          )}
          {(aiDraft.provider ?? ai.provider) === 'vertex' && (
            <div className="row" style={{ marginTop: 10, flexWrap: 'wrap' }}>
              <label className="editor-field">GCP project id
                <input type="text" value={aiDraft.vertex_project ?? ''}
                       onChange={(e) => setAiDraft({ ...aiDraft, vertex_project: e.target.value })} />
              </label>
              <label className="editor-field">Region
                <input type="text" placeholder="us-east5"
                       value={aiDraft.vertex_region ?? ''}
                       onChange={(e) => setAiDraft({ ...aiDraft, vertex_region: e.target.value })} />
              </label>
              <p className="hint" style={{ width: '100%' }}>
                Authenticates via Google Application Default Credentials on the server
                (gcloud auth or a service-account key file).
              </p>
            </div>
          )}
          {(aiDraft.provider ?? ai.provider) === 'bedrock' && (
            <div className="row" style={{ marginTop: 10, flexWrap: 'wrap' }}>
              <label className="editor-field">AWS access key
                <input type="password"
                       placeholder={ai.has_aws_keys
                         ? 'stored — leave blank to keep' : 'AKIA…'}
                       value={aiDraft.aws_access_key ?? ''}
                       onChange={(e) => setAiDraft({ ...aiDraft, aws_access_key: e.target.value })} />
              </label>
              <label className="editor-field">AWS secret key
                <input type="password"
                       placeholder={ai.has_aws_keys
                         ? 'stored — leave blank to keep' : ''}
                       value={aiDraft.aws_secret_key ?? ''}
                       onChange={(e) => setAiDraft({ ...aiDraft, aws_secret_key: e.target.value })} />
              </label>
              <label className="editor-field">Region
                <input type="text" placeholder={ai.default_aws_region || 'us-west-2'}
                       value={aiDraft.aws_region ?? ''}
                       onChange={(e) => setAiDraft({ ...aiDraft, aws_region: e.target.value })} />
              </label>
              <p className="hint" style={{ width: '100%' }}>
                Keys are optional — without them the server's AWS default credential
                chain is used{ai.bedrock_default
                  ? ' (this deployment runs on the service IAM role — no keys needed)'
                  : ' (instance role, profile)'}.
                Use inference-profile IDs (the <code>us.</code> prefix) — newer Claude
                models reject base model IDs.
              </p>
            </div>
          )}

          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" onClick={saveAi} disabled={busy}>
              Save AI model
            </button>
            <button className="ghost" onClick={testAi} disabled={busy}>
              Test model
            </button>
            <span className="hint">
              {ai.active
                ? `Active via ${
                    ai.source === 'workspace' ? 'workspace credentials'
                    : ai.source === 'iam-role' ? "the service's AWS IAM role (no keys)"
                    : "the server's environment key"}.`
                : 'Not active yet — the agent runs on deterministic priors until credentials are provided.'}
            </span>
          </div>
          {aiTest && (
            <p className="hint" style={{ marginTop: 8, whiteSpace: 'pre-wrap',
                                         color: aiTest.ok ? 'var(--ok, #2e7d32)'
                                                          : 'var(--err, #c62828)' }}>
              {aiTest.ok
                ? `✓ ${aiTest.model} responded: ${aiTest.reply}`
                : `✗ ${aiTest.model || 'model'} failed: ${aiTest.error}`}
            </p>
          )}
          </div>
        </div>
      )}

      {props.selected && props.selected !== 'ai_model' && (
        <div className="dl-modal-backdrop" onClick={() => props.onSelect(null)}>
          <div className="dl-modal" onClick={(e) => e.stopPropagation()}>
            <button className="dl-modal-close" onClick={() => props.onSelect(null)}
                    aria-label="Close">✕</button>
            <ElementDetail
              element={props.selected}
              state={state}
              onRun={runNow}
              running={busy}
              onEditModel={props.onEditModel}
              onScheduleSaved={refresh}
            />
          </div>
        </div>
      )}

      {showModel && (
        <>
          <DataModelView canEdit />
          {spec && (
            <div className="card" style={{ marginTop: 16 }}>
              <h3>
                Transformations — created by the AI agent, confirmed by{' '}
                {model.confirmed_by ?? '—'}
              </h3>
              <p className="hint">
                Case = {spec.case_id.source}. {spec.case_id.description} Every transformation
                below is the exact logic the load runs — click to see the SQL; edit via
                Mapping review.
              </p>
              {spec.activities.map((a) => (
                <div className={'activity ' + a.confidence} key={a.name}>
                  <div className="head">
                    <span className="name">{a.name}</span>
                    <span className={'badge ' + a.confidence}>{a.confidence} confidence</span>
                  </div>
                  <div className="meta">
                    {a.source_table} · {a.condition} · timestamp: {a.timestamp}
                  </div>
                  <details className="sql">
                    <summary>SQL</summary>
                    <pre>{a.sql}</pre>
                  </details>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </>
  )
}
