import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

/* The confirmation step as a short interview (§4).

   A radio button cannot hold "it depends — for services POs we raise the PO after
   the invoice", so that reasoning gets flattened into a wrong answer and nobody
   notices. This panel lets the SME say it in their own words.

   It never records the answer itself. It proposes one; the human presses "Use this
   answer", which fills the same pendingAnswers slot the radio buttons fill, and the
   existing /api/mapping/answers gate still does the recording. */

interface Turn { role: 'agent' | 'sme'; text: string }
interface TurnResp {
  reply: string
  proposed_answer: string | null
  rationale: string | null
  options: string[]
  ai_available: boolean
}

export default function Interviewer(
  { questionId, onAccept }: { questionId: string; onAccept: (answer: string) => void },
) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [proposal, setProposal] = useState<{ answer: string; rationale: string | null } | null>(null)
  const [aiAvailable, setAiAvailable] = useState(true)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api<TurnResp>('/api/mapping/interview', {
      method: 'POST', body: JSON.stringify({ question_id: questionId }),
    })
      .then((r) => { setTurns([{ role: 'agent', text: r.reply }]); setAiAvailable(r.ai_available) })
      .catch((e) => setError(e.message))
  }, [questionId])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [turns, proposal])

  async function send() {
    const message = input.trim()
    if (!message || busy) return
    const history = turns
    setTurns([...turns, { role: 'sme', text: message }])
    setInput(''); setBusy(true); setError(''); setProposal(null)
    try {
      const r = await api<TurnResp>('/api/mapping/interview', {
        method: 'POST',
        body: JSON.stringify({ question_id: questionId, message, history }),
      })
      setTurns((t) => [...t, { role: 'agent', text: r.reply }])
      setAiAvailable(r.ai_available)
      if (r.proposed_answer) setProposal({ answer: r.proposed_answer, rationale: r.rationale })
    } catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="iv">
      <div className="iv-log">
        {turns.map((t, i) => (
          <div className={'iv-msg ' + t.role} key={i}>
            <div className="bubble">{t.text}</div>
          </div>
        ))}
        {busy && <div className="iv-msg agent"><div className="bubble">Thinking…</div></div>}
        <div ref={endRef} />
      </div>

      {proposal && (
        <div className="iv-proposal">
          <div>
            <strong>I'll record:</strong> {proposal.answer}
            {proposal.rationale && <div className="hint" style={{ marginTop: 3 }}>{proposal.rationale}</div>}
          </div>
          <button className="primary" onClick={() => onAccept(proposal.answer)}>
            Use this answer
          </button>
        </div>
      )}

      {error && <p className="hint" style={{ color: 'var(--bad)' }}>{error}</p>}
      {!aiAvailable && (
        <p className="hint">
          No AI model configured, so free text can't be interpreted here — pick an option above,
          or set a model on the Data load page.
        </p>
      )}

      <div className="iv-input">
        <input
          type="text" value={input} placeholder="Answer in your own words…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') send() }}
        />
        <button className="ghost" onClick={send} disabled={busy || !input.trim()}>Send</button>
      </div>
    </div>
  )
}
