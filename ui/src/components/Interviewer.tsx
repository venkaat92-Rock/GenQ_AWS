import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import {
  canListen, canSpeak, createRecognizer, Recognizer, speak, stopSpeaking, warmVoices,
} from '../lib/voice'

/* The confirmation step as a short interview (§4).

   A radio button cannot hold "it depends — for services POs we raise the PO after
   the invoice", so that reasoning gets flattened into a wrong answer and nobody
   notices. This panel lets the SME say it in their own words — typed or spoken.

   It never records the answer itself. It proposes one; the human presses "Use this
   answer", which fills the same pendingAnswers slot the radio buttons fill, and the
   existing /api/mapping/answers gate still does the recording.

   Voice is strictly additive: every control here works with the keyboard and the
   screen alone. Speech output can be muted, speech input is Chromium/Safari only,
   and when either is unavailable the panel is exactly the typed interview it was. */

interface Turn { role: 'agent' | 'sme'; text: string }
interface TurnResp {
  reply: string
  proposed_answer: string | null
  rationale: string | null
  options: string[]
  ai_available: boolean
}

const VOICE_PREF = 'genq_interview_speak'

export default function Interviewer(
  { questionId, onAccept }: { questionId: string; onAccept: (answer: string) => void },
) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [proposal, setProposal] = useState<{ answer: string; rationale: string | null } | null>(null)
  const [aiAvailable, setAiAvailable] = useState(true)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [voiceOut, setVoiceOut] = useState(() => localStorage.getItem(VOICE_PREF) !== 'off')
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [voiceNote, setVoiceNote] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  const recognizer = useRef<Recognizer | null>(null)
  const spokenRef = useRef<Set<number>>(new Set())

  const speechOut = canSpeak()
  const speechIn = canListen()

  useEffect(() => { warmVoices() }, [])

  useEffect(() => {
    api<TurnResp>('/api/mapping/interview', {
      method: 'POST', body: JSON.stringify({ question_id: questionId }),
    })
      .then((r) => { setTurns([{ role: 'agent', text: r.reply }]); setAiAvailable(r.ai_available) })
      .catch((e) => setError(e.message))
    // Leaving the panel must never leave a voice talking or a mic open.
    return () => { stopSpeaking(); recognizer.current?.stop() }
  }, [questionId])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [turns, proposal, interim])

  // Read each new agent turn aloud once — never twice, even across re-renders.
  useEffect(() => {
    if (!voiceOut || !speechOut || listening) return
    const i = turns.length - 1
    if (i < 0 || turns[i].role !== 'agent' || spokenRef.current.has(i)) return
    spokenRef.current.add(i)
    speak(turns[i].text)
  }, [turns, voiceOut, speechOut, listening])

  function toggleVoiceOut() {
    const next = !voiceOut
    setVoiceOut(next)
    localStorage.setItem(VOICE_PREF, next ? 'on' : 'off')
    if (!next) stopSpeaking()
  }

  function toggleMic() {
    if (listening) { recognizer.current?.stop(); return }
    setError(''); setVoiceNote('')
    const rec = createRecognizer({
      onInterim: setInterim,
      onFinal: (text) => {
        setInterim('')
        setInput((prev) => (prev ? `${prev} ${text}` : text).trim())
      },
      onError: (message) => setVoiceNote(message),
      onEnd: () => { setListening(false); setInterim('') },
    })
    if (!rec) { setVoiceNote('This browser cannot do speech input — try Chrome or Edge.'); return }
    recognizer.current = rec
    setListening(true)
    rec.start()
  }

  async function send() {
    const message = input.trim()
    if (!message || busy) return
    recognizer.current?.stop()
    stopSpeaking()
    const history = turns
    setTurns([...turns, { role: 'sme', text: message }])
    setInput(''); setInterim(''); setBusy(true); setError(''); setProposal(null)
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
      {(speechOut || speechIn) && (
        <div className="iv-bar">
          {speechOut && (
            <button
              className={'iv-icon' + (voiceOut ? ' on' : '')}
              onClick={toggleVoiceOut}
              aria-pressed={voiceOut}
              title={voiceOut ? 'Questions are read aloud — click to mute' : 'Questions are muted — click to hear them'}
            >
              {voiceOut ? '🔊' : '🔇'} <span>{voiceOut ? 'Reading aloud' : 'Muted'}</span>
            </button>
          )}
          {speechOut && turns.length > 0 && voiceOut && (
            <button className="iv-icon" onClick={() => {
              const last = [...turns].reverse().find((t) => t.role === 'agent')
              if (last) speak(last.text)
            }} title="Repeat the last question">↺ <span>Repeat</span></button>
          )}
          <span className="hint" style={{ marginLeft: 'auto' }}>
            {speechIn ? 'Speak or type — both work.' : 'Typing only in this browser.'}
          </span>
        </div>
      )}

      <div className="iv-log">
        {turns.map((t, i) => (
          <div className={'iv-msg ' + t.role} key={i}>
            <div className="bubble">{t.text}</div>
            {t.role === 'agent' && speechOut && (
              <button className="iv-replay" onClick={() => speak(t.text)}
                      title="Read this aloud" aria-label="Read this aloud">🔊</button>
            )}
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
          <button className="primary" onClick={() => { stopSpeaking(); onAccept(proposal.answer) }}>
            Use this answer
          </button>
        </div>
      )}

      {error && <p className="hint" style={{ color: 'var(--bad)', padding: '0 14px' }}>{error}</p>}
      {voiceNote && <p className="hint" style={{ padding: '0 14px' }}>{voiceNote}</p>}
      {!aiAvailable && (
        <p className="hint" style={{ padding: '0 14px' }}>
          No AI model configured, so free text can't be interpreted here — pick an option above,
          or set a model on the Data load page.
        </p>
      )}

      <div className="iv-input">
        {speechIn && (
          <button
            className={'iv-mic' + (listening ? ' live' : '')}
            onClick={toggleMic}
            aria-pressed={listening}
            title={listening ? 'Stop dictating' : 'Answer by voice'}
          >
            {listening ? '⏹' : '🎤'}
          </button>
        )}
        <input
          type="text"
          value={interim ? `${input}${input ? ' ' : ''}${interim}` : input}
          placeholder={listening ? 'Listening…' : 'Answer in your own words…'}
          onChange={(e) => { setInterim(''); setInput(e.target.value) }}
          onKeyDown={(e) => { if (e.key === 'Enter') send() }}
        />
        <button className="ghost" onClick={send} disabled={busy || !input.trim()}>Send</button>
      </div>
    </div>
  )
}
