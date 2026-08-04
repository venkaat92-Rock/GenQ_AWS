import { useEffect, useState } from 'react'
import { api } from '../api'

/* One AI backend for the whole tool: ProcessIQ's modelling agent, the co-pilot,
   and DiagramIQ's notes analysis all resolve through /api/ai-provider. This panel
   is the single control for it, rendered in both offerings so the choice and the
   model list can never drift between them. */

export interface BedrockModel { id: string; label: string; note: string }

export interface AiSettings {
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
  bedrock_models?: BedrockModel[]
  bedrock_default?: boolean
  default_aws_region?: string
}

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: 'Anthropic API', vertex: 'Google Vertex AI', bedrock: 'AWS Bedrock',
}

const MODEL_SUGGESTIONS: Record<string, string[]> = {
  anthropic: ['claude-opus-4-5', 'claude-sonnet-4-5', 'claude-haiku-4-5'],
  vertex: ['claude-opus-4-5@20251101', 'claude-sonnet-4-5@20250929'],
  bedrock: [],
}

export function describeSource(ai: AiSettings): string {
  if (!ai.active) return 'Not active — the agent runs on deterministic priors only.'
  return ai.source === 'iam-role' ? "Active via the service's AWS IAM role (no keys stored)."
    : ai.source === 'workspace' ? 'Active via workspace credentials.'
      : "Active via the server's environment key."
}

export default function AiProviderPanel(
  { onLoaded }: { onLoaded?: (ai: AiSettings) => void },
) {
  const [ai, setAi] = useState<AiSettings | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [test, setTest] = useState<{ ok: boolean; model: string; reply?: string; error?: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function load() {
    api<AiSettings>('/api/ai-provider')
      .then((s) => { setAi(s); onLoaded?.(s) })
      .catch((e) => setError(e.message))
  }
  useEffect(load, [])

  if (error && !ai) return <p className="hint">Could not load AI settings — {error}</p>
  if (!ai) return <p className="hint">Loading AI settings…</p>

  const provider = draft.provider ?? ai.provider
  const models = provider === 'bedrock' && ai.bedrock_models?.length
    ? ai.bedrock_models.map((m) => ({ id: m.id, hint: [m.label, m.note].filter(Boolean).join(' — ') }))
    : (MODEL_SUGGESTIONS[provider] ?? []).map((m) => ({ id: m, hint: '' }))

  // Only send secrets the user actually typed: omitted fields keep the stored value.
  function payload() {
    const body: Record<string, string> = { provider, model: draft.model ?? ai!.model }
    for (const k of ['anthropic_key', 'vertex_project', 'vertex_region',
                     'aws_access_key', 'aws_secret_key', 'aws_region']) {
      if (draft[k] !== undefined) body[k] = draft[k]
    }
    return body
  }

  async function save() {
    setBusy(true); setError(''); setTest(null)
    try {
      const s = await api<AiSettings>('/api/ai-provider',
        { method: 'PUT', body: JSON.stringify(payload()) })
      setAi(s); setDraft({}); onLoaded?.(s)
    } catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }

  async function runTest() {
    setBusy(true); setError(''); setTest(null)
    try {
      setTest(await api('/api/ai-provider/test',
        { method: 'POST', body: JSON.stringify(payload()) }))
    } catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="filter-panel" style={{ marginTop: 10 }}>
      <div className="row" style={{ flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label className="editor-field">Provider
          <select value={provider}
                  onChange={(e) => setDraft({ ...draft, provider: e.target.value })}>
            {Object.entries(PROVIDER_LABEL).map(([key, label]) => (
              <option key={key} value={key} disabled={ai.sdk[key] === false}>
                {label}{ai.sdk[key] === false ? ' (SDK not installed)' : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="editor-field">Model
          <input type="text" list="ai-provider-models" style={{ minWidth: 300 }}
                 value={draft.model ?? ai.model}
                 onChange={(e) => setDraft({ ...draft, model: e.target.value })} />
          <datalist id="ai-provider-models">
            {models.map((m) => <option key={m.id} value={m.id} label={m.hint || undefined} />)}
          </datalist>
        </label>
      </div>

      {provider === 'anthropic' && (
        <div className="row" style={{ marginTop: 10, flexWrap: 'wrap' }}>
          <label className="editor-field">API key
            <input type="password" style={{ minWidth: 300 }}
                   placeholder={ai.has_anthropic_key ? 'stored — leave blank to keep' : 'sk-ant-…'}
                   value={draft.anthropic_key ?? ''}
                   onChange={(e) => setDraft({ ...draft, anthropic_key: e.target.value })} />
          </label>
        </div>
      )}
      {provider === 'vertex' && (
        <div className="row" style={{ marginTop: 10, flexWrap: 'wrap' }}>
          <label className="editor-field">GCP project id
            <input type="text" value={draft.vertex_project ?? ai.vertex_project ?? ''}
                   onChange={(e) => setDraft({ ...draft, vertex_project: e.target.value })} />
          </label>
          <label className="editor-field">Region
            <input type="text" placeholder="us-east5"
                   value={draft.vertex_region ?? ai.vertex_region ?? ''}
                   onChange={(e) => setDraft({ ...draft, vertex_region: e.target.value })} />
          </label>
        </div>
      )}
      {provider === 'bedrock' && (
        <div className="row" style={{ marginTop: 10, flexWrap: 'wrap' }}>
          <label className="editor-field">AWS access key
            <input type="password"
                   placeholder={ai.has_aws_keys ? 'stored — leave blank to keep' : 'AKIA… (optional)'}
                   value={draft.aws_access_key ?? ''}
                   onChange={(e) => setDraft({ ...draft, aws_access_key: e.target.value })} />
          </label>
          <label className="editor-field">AWS secret key
            <input type="password"
                   placeholder={ai.has_aws_keys ? 'stored — leave blank to keep' : '(optional)'}
                   value={draft.aws_secret_key ?? ''}
                   onChange={(e) => setDraft({ ...draft, aws_secret_key: e.target.value })} />
          </label>
          <label className="editor-field">Region
            <input type="text" placeholder={ai.default_aws_region || 'us-west-2'}
                   value={draft.aws_region ?? ai.aws_region ?? ''}
                   onChange={(e) => setDraft({ ...draft, aws_region: e.target.value })} />
          </label>
          <p className="hint" style={{ width: '100%' }}>
            Keys are optional — without them the server's AWS default credential chain is
            used{ai.bedrock_default
              ? ' (this deployment runs on the service IAM role, so no keys are needed)'
              : ' (instance role, profile)'}.
            Use inference-profile IDs (the <code>us.</code> prefix) — newer Claude models
            reject base model IDs.
          </p>
        </div>
      )}

      <div className="row" style={{ marginTop: 12, flexWrap: 'wrap' }}>
        <button className="primary" onClick={save} disabled={busy}>Save AI model</button>
        <button className="ghost" onClick={runTest} disabled={busy}>Test model</button>
        <span className="hint">{describeSource(ai)}</span>
      </div>
      {error && <p className="hint" style={{ marginTop: 8, color: 'var(--danger, #b3452b)' }}>{error}</p>}
      {test && (
        <p className="hint" style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>
          {test.ok
            ? `✓ ${test.model} replied: ${test.reply}`
            : `✗ ${test.model} — ${test.error}`}
        </p>
      )}
    </div>
  )
}
