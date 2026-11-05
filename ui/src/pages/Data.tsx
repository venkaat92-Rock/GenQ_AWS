import { useEffect, useRef, useState } from 'react'
import { api, getApiKey, uploadFile } from '../api'
import ContextDocuments from '../components/ContextDocuments'

type Schema = Record<string, { columns: { name: string; type: string }[]; row_count: number }>

interface ConnectorSummary {
  id: string
  name: string
  system: string
  process: string
  agent_support: 'full' | 'partial' | 'template'
  description: string
  table_count: number
}
interface LiveField {
  key: string
  label: string
  placeholder?: string
  secret?: boolean
  list?: boolean
  optional?: boolean
}
interface ConnectorDetail extends ConnectorSummary {
  export_hint: string
  tables: { name: string; description: string; columns: string[]; required: boolean }[]
  live?: {
    kind: string
    hint: string
    defaults: Record<string, string | string[]>
    fields: LiveField[]
  }
}

const SUPPORT_BADGE = { full: 'high', partial: 'medium', template: 'status' } as const

export default function DataPage(props: { onContinue: () => void }) {
  const [schema, setSchema] = useState<Schema>({})
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const [connectors, setConnectors] = useState<ConnectorSummary[]>([])
  const [detail, setDetail] = useState<ConnectorDetail | null>(null)
  const [live, setLive] = useState<Record<string, string>>({ kind: 'servicenow' })
  const [saveName, setSaveName] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [showLive, setShowLive] = useState(false)
  const [saved, setSaved] = useState<{ name: string; kind: string; auto_refresh: boolean }[]>([])

  async function refresh() {
    try {
      setSchema(await api<Schema>('/api/sources'))
    } catch (e: any) {
      setError(e.message)
    }
  }

  useEffect(() => {
    refresh()
    api<{ connectors: ConnectorSummary[] }>('/api/connectors')
      .then((r) => setConnectors(r.connectors))
      .catch(() => {})
    api<{ connections: any[] }>('/api/connections')
      .then((r) => setSaved(r.connections))
      .catch(() => {})
  }, [])

  const openConnector = (id: string) =>
    api<ConnectorDetail>(`/api/connectors/${id}`)
      .then((d) => {
        setDetail(d)
        if (d.live) {
          // prefill the instance form with the connector's tables/entities
          const prefill: Record<string, string> = { kind: d.live.kind }
          for (const [k, v] of Object.entries(d.live.defaults)) {
            prefill[k] = Array.isArray(v) ? v.join(', ') : String(v)
          }
          setLive(prefill)
          setSaveName(d.id)
        }
      })
      .catch((e) => setError(e.message))

  const downloadTemplates = (id: string) => {
    const key = getApiKey()
    fetch(`/api/connectors/${id}/templates`, { headers: key ? { 'X-API-Key': key } : {} })
      .then((r) => r.blob())
      .then((blob) => {
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `${id}-csv-templates.zip`
        a.click()
        URL.revokeObjectURL(url)
      })
  }

  const refreshSaved = () =>
    api<{ connections: any[] }>('/api/connections').then((r) => setSaved(r.connections)).catch(() => {})

  async function runExtract() {
    setBusy(true)
    setError('')
    try {
      const listy = (v?: string) => (v ?? '').split(',').map((t) => t.trim()).filter(Boolean)
      const config: Record<string, unknown> = {
        connection: live.connection?.trim(),
        tables: listy(live.tables),
        schema_name: (live.schema_name ?? live.schema)?.trim() || null,
        table: live.table?.trim(),
        instance_url: live.instance_url?.trim(),
        user: live.user?.trim(),
        password: live.password,
        base_url: live.base_url?.trim(),
        email: live.email?.trim(),
        token: live.token,
        jql: live.jql,
        access_token: live.access_token,
        objects: listy(live.objects),
        service_url: live.service_url?.trim(),
        entities: listy(live.entities),
      }
      const r = await api<{ staged: Record<string, number>; saved_as?: string }>(
        '/api/connectors/extract',
        {
          method: 'POST',
          body: JSON.stringify({ kind: live.kind, config, name: saveName.trim(),
                                 auto_refresh: autoRefresh }),
        },
      )
      setNotice(
        'Extracted: ' +
          Object.entries(r.staged).map(([t, n]) => `${t} (${n} rows)`).join(', ') +
          (r.saved_as ? ` — saved as "${r.saved_as}" for scheduled refresh.` : ''),
      )
      await refresh()
      await refreshSaved()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const rerunSaved = async (name: string) => {
    setBusy(true)
    setError('')
    try {
      const r = await api<{ staged: Record<string, number> }>(
        `/api/connections/${encodeURIComponent(name)}/run`, { method: 'POST' })
      setNotice('Re-extracted "' + name + '": ' +
        Object.entries(r.staged).map(([t, n]) => `${t} (${n} rows)`).join(', '))
      await refresh()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const deleteSaved = async (name: string) => {
    await api(`/api/connections/${encodeURIComponent(name)}`, { method: 'DELETE' }).catch(() => {})
    await refreshSaved()
  }

  async function loadDemo(dataset: 'p2p' | 'o2c' | 'ford_p2p') {
    setBusy(true)
    setError('')
    try {
      const r = await api<{ note: string }>(`/api/sources/demo?dataset=${dataset}`, {
        method: 'POST',
      })
      setNotice(`Staged: ${r.note}.`)
      await refresh()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function onFiles(files: FileList | null) {
    if (!files || files.length === 0) return
    setBusy(true)
    setError('')
    setNotice('')
    const failures: string[] = []
    let staged = 0
    for (const file of Array.from(files)) {
      try {
        await uploadFile(file)
        staged += 1
      } catch (e: any) {
        failures.push(`${file.name}: ${e.message}`)
      }
    }
    if (staged > 0) setNotice(`${staged} file(s) staged.`)
    if (failures.length > 0) setError(failures.join(' · '))
    await refresh()
    setBusy(false)
    if (fileInput.current) fileInput.current.value = ''
  }

  const tables = Object.entries(schema)

  return (
    <>
      <h1>Source data</h1>
      <p className="lead">
        Connect a source system or stage table extracts. The modelling agent reads the
        schema and sample rows; no transformation runs until a mapping is confirmed.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}
      {tables.length > 0 && (
        <div className="row" style={{ marginBottom: 14 }}>
          <button className="primary" onClick={props.onContinue}>
            Continue to mapping review
          </button>
        </div>
      )}

      <div className="card">
        <h3>Connect a source</h3>
        <p className="hint" style={{ marginBottom: 10 }}>
          Establish a live connection to the source system, or select a process connector
          for guided table extraction. Support levels: full — the agent drafts the complete
          mapping; partial — a subset of events; template — guided extraction.
        </p>
        <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
          <button className={'chip clickable' + (showLive ? ' on' : '')}
                  onClick={() => setShowLive(!showLive)}>
            Live connection (API / database / URL) <span className="badge high">live</span>
          </button>
          {connectors.map((c) => (
            <button
              key={c.id}
              className={'chip clickable' + (detail?.id === c.id ? ' on' : '')}
              onClick={() => openConnector(c.id)}
            >
              {c.name} <span className={'badge ' + SUPPORT_BADGE[c.agent_support]}>{c.agent_support}</span>
            </button>
          ))}
        </div>

        {detail && (
          <div className="filter-panel" style={{ marginTop: 12 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <strong>{detail.name}</strong>
              <button className="linklike" onClick={() => setDetail(null)}>Close</button>
            </div>
            <p className="hint">{detail.description}</p>

            {detail.live && (
              <div className="subcard" style={{ margin: '8px 0' }}>
                <strong>Connect your instance</strong>
                <p className="hint">{detail.live.hint}</p>
                <div className="row" style={{ flexWrap: 'wrap' }}>
                  {detail.live.fields.map((f) => (
                    <label key={f.key} className="editor-field"
                           style={f.list || f.placeholder && f.placeholder.length > 30
                             ? { minWidth: 320, flex: 1 } : undefined}>
                      {f.label}{f.list ? ' (comma-separated)' : ''}
                      <input type={f.secret ? 'password' : 'text'}
                             placeholder={f.placeholder ?? ''}
                             value={live[f.key] ?? ''}
                             onChange={(e) => setLive({ ...live,
                               kind: detail.live!.kind, [f.key]: e.target.value })} />
                    </label>
                  ))}
                </div>
                <div className="row" style={{ marginTop: 10 }}>
                  <input type="text" placeholder="Save connection as"
                         style={{ maxWidth: 180 }} value={saveName}
                         onChange={(e) => setSaveName(e.target.value)} />
                  <label className="row" style={{ gap: 5, fontSize: 13 }}>
                    <input type="checkbox" checked={autoRefresh}
                           onChange={(e) => setAutoRefresh(e.target.checked)} />
                    re-extract on schedule
                  </label>
                  <button className="primary" onClick={runExtract} disabled={busy}>
                    {busy ? 'Extracting…' : 'Connect & extract'}
                  </button>
                </div>
              </div>
            )}

            <details style={{ marginTop: 8 }}>
              <summary className="hint" style={{ cursor: 'pointer' }}>
                No API access? Download CSV templates and stage the exports instead.
              </summary>
              <p className="hint" style={{ marginTop: 6 }}><em>{detail.export_hint}</em>{' '}
                <button className="ghost" onClick={() => downloadTemplates(detail.id)}>
                  Download CSV templates
                </button>
              </p>
            </details>

            <table style={{ marginTop: 8 }}>
              <thead>
                <tr><th>Table</th><th>What it is</th><th>Key columns</th><th></th></tr>
              </thead>
              <tbody>
                {detail.tables.map((t) => (
                  <tr key={t.name}>
                    <td><code className="inline">{t.name}</code></td>
                    <td>{t.description}</td>
                    <td className="variant-path">{t.columns.join(', ')}</td>
                    <td>{t.required ? <span className="badge high">required</span> :
                         <span className="badge status">optional</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {showLive && (
          <div className="filter-panel" style={{ marginTop: 12 }}>
            <div className="filter-group">
              <span className="filter-label">Live connection to the source system</span>
              <div className="row">
                <select value={live.kind} onChange={(e) => setLive({ kind: e.target.value })}>
                  <option value="servicenow">ServiceNow (Table API)</option>
                  <option value="jira">Jira Cloud (REST)</option>
                  <option value="salesforce">Salesforce (REST / SOQL)</option>
                  <option value="odata">OData — SAP Gateway / Dynamics 365</option>
                  <option value="postgres">PostgreSQL (DSN)</option>
                  <option value="mysql">MySQL (DSN)</option>
                  <option value="sqlite">SQLite file (server path)</option>
                  <option value="url">CSV from URL</option>
                </select>
              </div>
              {(() => {
                const F = (key: string, placeholder: string, type = 'text', wide = false) => (
                  <input type={type} placeholder={placeholder}
                         style={wide ? { minWidth: 320 } : undefined}
                         value={live[key] ?? ''}
                         onChange={(e) => setLive({ ...live, [key]: e.target.value })} />
                )
                switch (live.kind) {
                  case 'servicenow':
                    return <div className="row">{F('instance_url', 'https://yourco.service-now.com', 'text', true)}{F('user', 'username')}{F('password', 'password', 'password')}{F('tables', 'tables: incident, sys_audit', 'text', true)}</div>
                  case 'jira':
                    return <div className="row">{F('base_url', 'https://yourco.atlassian.net', 'text', true)}{F('email', 'you@company.com')}{F('token', 'API token', 'password')}{F('jql', 'JQL filter (optional)', 'text', true)}</div>
                  case 'salesforce':
                    return <div className="row">{F('instance_url', 'https://yourco.my.salesforce.com', 'text', true)}{F('access_token', 'access token', 'password')}{F('objects', 'objects: Opportunity, OpportunityFieldHistory', 'text', true)}</div>
                  case 'odata':
                    return <div className="row">{F('service_url', 'https://sap-gw/sap/opu/odata/sap/ZSRV/', 'text', true)}{F('user', 'user (optional)')}{F('password', 'password', 'password')}{F('token', 'or bearer token', 'password')}{F('entities', 'entity sets: PurchaseOrderSet', 'text', true)}</div>
                  case 'url':
                    return <div className="row">{F('connection', 'https://…/export.csv', 'text', true)}{F('table', 'target table name (e.g. VBAK)')}</div>
                  default:
                    return <div className="row">{F('connection', live.kind === 'sqlite' ? 'C:\\path\\to\\erp.db' : 'host=… dbname=… user=… password=…', 'text', true)}{F('tables', 'tables: EKKO, EKPO, RBKP', 'text', true)}{live.kind === 'postgres' && F('schema', 'schema (default public)')}</div>
                }
              })()}
              <div className="row">
                <input type="text" placeholder="save as… (optional, enables scheduled refresh)"
                       style={{ minWidth: 260 }}
                       value={saveName} onChange={(e) => setSaveName(e.target.value)} />
                {saveName.trim() && (
                  <label className="row" style={{ gap: 6, fontSize: 13 }}>
                    <input type="checkbox" checked={autoRefresh}
                           onChange={(e) => setAutoRefresh(e.target.checked)} />
                    re-extract before every scheduled load
                  </label>
                )}
                <button className="primary" onClick={runExtract} disabled={busy}>
                  {busy ? 'Extracting…' : 'Extract now'}
                </button>
              </div>
              <p className="hint">
                Read-only extraction. Credentials are used for this request — and stored
                with the connection only if you save it (needed for scheduled refresh;
                v1 stores them server-side un-encrypted, so use least-privilege accounts).
              </p>
            </div>
          </div>
        )}

        {saved.length > 0 && (
          <div className="filter-panel" style={{ marginTop: 12 }}>
            <span className="filter-label">Saved connections</span>
            <table>
              <tbody>
                {saved.map((c) => (
                  <tr key={c.name}>
                    <td><strong>{c.name}</strong></td>
                    <td>{c.kind}</td>
                    <td>{c.auto_refresh ? <span className="badge high">auto-refresh</span>
                          : <span className="badge status">manual</span>}</td>
                    <td className="row" style={{ gap: 8 }}>
                      <button className="linklike" onClick={() => rerunSaved(c.name)}>re-extract</button>
                      <button className="linklike" onClick={() => deleteSaved(c.name)}>✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h3>Upload table extracts</h3>
        <div className="row">
          <button className="ghost" onClick={() => fileInput.current?.click()} disabled={busy}>
            Select CSV files
          </button>
          <span className="hint">One file per source table. Filenames become table names.</span>
          <input
            ref={fileInput}
            type="file"
            accept=".csv"
            multiple
            hidden
            onChange={(e) => onFiles(e.target.files)}
          />
        </div>
        <details className="samples">
          <summary>Evaluation datasets (synthetic, for product evaluation only)</summary>
          <div className="row">
            <button className="ghost" onClick={() => loadDemo('p2p')} disabled={busy}>
              Purchasing (P2P) sample
            </button>
            <button className="ghost" onClick={() => loadDemo('o2c')} disabled={busy}>
              Order management (O2C) sample
            </button>
            <button className="ghost" onClick={() => loadDemo('ford_p2p')} disabled={busy}
                    title="360 purchase orders, 7 vendor categories, chip-shortage delays, price disputes, maverick buying — a messy real-world P2P process">
              Ford Motors P2P (chaotic, enterprise-scale)
            </button>
          </div>
        </details>
      </div>

      <ContextDocuments />

      {tables.length > 0 && (
        <div className="card">
          <h3>Staged tables ({tables.length})</h3>
          <table>
            <thead>
              <tr>
                <th>Table</th>
                <th>Rows</th>
                <th>Columns</th>
              </tr>
            </thead>
            <tbody>
              {tables.map(([name, info]) => (
                <tr key={name}>
                  <td>
                    <code className="inline">{name}</code>
                  </td>
                  <td>{info.row_count}</td>
                  <td className="variant-path">
                    {info.columns.map((c) => c.name).join(', ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
