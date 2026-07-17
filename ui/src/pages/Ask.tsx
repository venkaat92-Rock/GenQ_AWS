import { useState } from 'react'
import { api } from '../api'

interface Answer {
  answer: string
  intent: string
  citations: {
    analysis: string
    cases_considered: number
    activities_used: string[]
    filters: string[]
  }
  data: unknown
}

const EXAMPLES = [
  'What does the process look like?',
  'Where is the biggest bottleneck?',
  'How long does it take end to end?',
  'How much rework do we have?',
  'How many cases deviate from the standard path?',
]

export default function AskPage() {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function ask(q: string) {
    setBusy(true)
    setError('')
    try {
      setAnswer(
        await api<Answer>('/api/ask', { method: 'POST', body: JSON.stringify({ question: q }) }),
      )
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h1>Ask your process</h1>
      <p className="lead">
        Plain-language questions over the confirmed event log. Every answer cites exactly which
        analysis, cases, and filters produced it — traceable, not magic.
      </p>
      {error && <div className="error-banner">{error}</div>}

      <div className="card">
        <div className="row">
          <input
            type="text"
            placeholder="e.g. Where is the biggest bottleneck?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && question.trim() && ask(question)}
          />
          <button
            className="primary"
            onClick={() => ask(question)}
            disabled={busy || !question.trim()}
          >
            {busy ? 'Analyzing…' : 'Ask'}
          </button>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              className="chip"
              style={{ cursor: 'pointer' }}
              onClick={() => {
                setQuestion(example)
                ask(example)
              }}
            >
              {example}
            </button>
          ))}
        </div>
      </div>

      {answer && (
        <>
          <div className="answer">{answer.answer}</div>
          <div className="card">
            <h3>Based on</h3>
            <div className="citations">
              <span className="chip">analysis: {answer.citations.analysis}</span>
              <span className="chip">cases: {answer.citations.cases_considered}</span>
              {answer.citations.filters.map((f) => (
                <span className="chip" key={f}>
                  filter: {f}
                </span>
              ))}
            </div>
            <p className="hint" style={{ marginTop: 10 }}>
              Activities considered: {answer.citations.activities_used.join(', ')}
            </p>
            <details className="sql" style={{ marginTop: 8 }}>
              <summary>Underlying data</summary>
              <pre>{JSON.stringify(answer.data, null, 2)}</pre>
            </details>
          </div>
        </>
      )}
    </>
  )
}
