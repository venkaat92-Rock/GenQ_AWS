import { useEffect, useRef, useState } from 'react'
import { api, uploadDocument } from '../api'

/* Optional SOPs, work instructions and screenshots that give the modelling agent
   the context a schema cannot carry.

   A schema says what the system recorded; an SOP says why. "AP blocks the invoice
   above a 2% price variance" explains a status code no amount of staring at RBKP
   reveals. This is strictly optional — with no documents the agent drafts from the
   schema exactly as before.

   The design point worth preserving: a file we could NOT read must never look like
   a file we read and ignored. Every document shows its status, and anything short
   of "read" says why in plain language. */

interface Doc {
  id: string
  filename: string
  kind: string
  status: 'read' | 'needs_ai' | 'unreadable'
  note: string
  chars: number
  truncated: boolean
  bytes: number
  uploaded_at: string
}

const BADGE: Record<Doc['status'], { cls: string; label: string }> = {
  read: { cls: 'high', label: 'read' },
  needs_ai: { cls: 'medium', label: 'needs AI' },
  unreadable: { cls: 'low', label: 'not read' },
}

const KB = (n: number) => (n < 1024 ? `${n} B` : `${Math.round(n / 1024)} KB`)

export default function ContextDocuments() {
  const [docs, setDocs] = useState<Doc[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)

  function refresh() {
    api<{ documents: Doc[] }>('/api/documents')
      .then((r) => setDocs(r.documents))
      .catch((e) => setError(e.message))
  }
  useEffect(refresh, [])

  async function onFiles(files: FileList | null) {
    if (!files?.length) return
    setBusy(true); setError('')
    try {
      for (const file of Array.from(files)) await uploadDocument(file)
      refresh()
    } catch (e: any) { setError(e.message) } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  async function remove(id: string) {
    setBusy(true); setError('')
    try { await api(`/api/documents/${id}`, { method: 'DELETE' }); refresh() }
    catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }

  const readable = docs.filter((d) => d.status === 'read').length

  return (
    <div className="card">
      <h3>
        Context documents <span className="badge status">optional</span>
      </h3>
      <p className="hint" style={{ marginTop: -4 }}>
        SOPs, work instructions, policy notes, screenshots — anything that describes how this
        process is <em>meant</em> to run. The agent reads them alongside your tables, so it can
        explain status codes and conditions the schema alone cannot reveal. Leave this empty and
        the mapping is drafted from the data exactly as before.
      </p>

      <div className="row">
        <button className="ghost" onClick={() => input.current?.click()} disabled={busy}>
          {busy ? 'Reading…' : 'Add documents'}
        </button>
        <span className="hint">
          Word, PDF, text, spreadsheets, images. Scans and screenshots are read by the AI model.
        </span>
        <input ref={input} type="file" multiple hidden
               accept=".docx,.pdf,.txt,.md,.rst,.log,.csv,.xlsx,.xlsm,.png,.jpg,.jpeg,.gif,.webp,.bmp,.tiff,.tif"
               onChange={(e) => onFiles(e.target.files)} />
      </div>

      {error && <p className="hint" style={{ color: 'var(--bad)' }}>{error}</p>}

      {docs.length > 0 && (
        <>
          <table style={{ marginTop: 12 }}>
            <thead>
              <tr><th>File</th><th>Type</th><th>Status</th><th>Extracted</th><th /></tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td>
                    {d.filename}
                    {d.note && <div className="hint" style={{ marginTop: 2 }}>{d.note}</div>}
                  </td>
                  <td className="hint">{d.kind} · {KB(d.bytes)}</td>
                  <td><span className={'badge ' + BADGE[d.status].cls}>{BADGE[d.status].label}</span></td>
                  <td className="hint">
                    {d.status === 'read' ? `${d.chars.toLocaleString()} chars${d.truncated ? ' (capped)' : ''}` : '—'}
                  </td>
                  <td>
                    <button className="ghost" onClick={() => remove(d.id)} disabled={busy}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint" style={{ marginTop: 8 }}>
            {readable === docs.length
              ? `All ${docs.length} document(s) will inform the next mapping draft.`
              : `${readable} of ${docs.length} will inform the next draft — the rest could not be read, see the reasons above.`}
            {' '}Re-draft the mapping after adding documents for them to take effect.
          </p>
        </>
      )}
    </div>
  )
}
