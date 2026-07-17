import { useEffect, useState } from 'react'
import { api } from '../api'

interface Reference {
  activities: string[]
  updated_by: string | null
  source: 'sme' | 'most_frequent_variant'
}

// Signavio-style conformance target, editable by the business SME — no analyst needed.
export default function ReferenceEditor(props: {
  allActivities: string[]
  onSaved: () => void
}) {
  const [ref, setRef] = useState<Reference | null>(null)
  const [draft, setDraft] = useState<string[]>([])
  const [reviewer, setReviewer] = useState('')
  const [dirty, setDirty] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api<Reference>('/api/reference').then((r) => {
      setRef(r)
      setDraft(r.activities)
    }).catch((e) => setError(e.message))
  }, [])

  if (!ref) return null

  const move = (i: number, dir: -1 | 1) => {
    const next = [...draft]
    const j = i + dir
    if (j < 0 || j >= next.length) return
    ;[next[i], next[j]] = [next[j], next[i]]
    setDraft(next)
    setDirty(true)
  }
  const remove = (i: number) => {
    setDraft(draft.filter((_, k) => k !== i))
    setDirty(true)
  }
  const add = (activity: string) => {
    if (activity) {
      setDraft([...draft, activity])
      setDirty(true)
    }
  }

  async function save() {
    setError('')
    try {
      const saved = await api<Reference>('/api/reference', {
        method: 'PUT',
        body: JSON.stringify({ activities: draft, updated_by: reviewer }),
      })
      setRef(saved)
      setDirty(false)
      setNotice(`Reference saved — conformance now measures against your target process.`)
      props.onSaved()
    } catch (e: any) {
      setError(e.message)
    }
  }

  return (
    <>
      <p className="hint" style={{ marginBottom: 10 }}>
        The expected ("to-be") process that conformance is measured against.{' '}
        {ref.source === 'sme'
          ? `Currently set by ${ref.updated_by}.`
          : 'Currently defaulting to the most frequent variant — adjust it to your target process and save.'}
      </p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}
      <ol className="ref-list">
        {draft.map((a, i) => (
          <li key={`${a}-${i}`}>
            <span>{a}</span>
            <span className="ref-actions">
              <button onClick={() => move(i, -1)} title="move up">↑</button>
              <button onClick={() => move(i, 1)} title="move down">↓</button>
              <button onClick={() => remove(i)} title="remove">✕</button>
            </span>
          </li>
        ))}
      </ol>
      <div className="row" style={{ marginTop: 10 }}>
        <select value="" onChange={(e) => add(e.target.value)}>
          <option value="">+ add activity…</option>
          {props.allActivities.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Your name (SME sign-off)"
          style={{ maxWidth: 220 }}
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
        />
        <button className="primary" onClick={save} disabled={!dirty || !reviewer.trim() || draft.length === 0}>
          Save reference
        </button>
      </div>
    </>
  )
}
