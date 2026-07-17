import { useState } from 'react'
import { api, saveWorkspace } from '../api'

export default function WorkspacePage(props: { onReady: () => void }) {
  const [name, setName] = useState('')
  const [existingKey, setExistingKey] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function create() {
    setBusy(true)
    setError('')
    try {
      const ws = await api<{ api_key: string; name: string }>('/api/workspaces', {
        method: 'POST',
        body: JSON.stringify({ name }),
      })
      saveWorkspace(ws.api_key, ws.name)
      props.onReady()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function connect() {
    setBusy(true)
    setError('')
    try {
      localStorage.setItem('processiq_api_key', existingKey.trim())
      const me = await api<{ name: string }>('/api/workspaces/me')
      saveWorkspace(existingKey.trim(), me.name)
      props.onReady()
    } catch (e: any) {
      localStorage.removeItem('processiq_api_key')
      setError('That key was not accepted: ' + e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="genq-hero">
        <h1>Gen<span>Q</span></h1>
        <p className="genq-caption">Gentic Mining</p>
      </div>
      <p className="lead">
        Agent-built process mining and BPMN diagram intelligence for teams that will never
        afford the big-enterprise stack. Create a workspace, connect your data, confirm what
        the agent proposes, and self-serve — in plain language.
      </p>
      {error && <div className="error-banner">{error}</div>}
      <div className="card">
        <h3>Create a workspace</h3>
        <div className="row">
          <input
            type="text"
            placeholder="Company or team name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button className="primary" onClick={create} disabled={busy || !name.trim()}>
            Create workspace
          </button>
        </div>
        <p className="hint" style={{ marginTop: 8 }}>
          You'll get an API key — store it somewhere safe; it is the only way back into this
          workspace.
        </p>
      </div>
      <div className="card">
        <h3>…or connect to an existing one</h3>
        <div className="row">
          <input
            type="text"
            placeholder="piq_..."
            value={existingKey}
            onChange={(e) => setExistingKey(e.target.value)}
          />
          <button className="ghost" onClick={connect} disabled={busy || !existingKey.trim()}>
            Connect
          </button>
        </div>
      </div>
    </>
  )
}
