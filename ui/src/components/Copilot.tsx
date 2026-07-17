import { useEffect, useRef, useState } from 'react'
import { api, getWorkspaceName } from '../api'

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: 'Anthropic API', vertex: 'Google Vertex AI', bedrock: 'AWS Bedrock',
}

interface KpiProposal {
  name: string
  definition: Record<string, unknown>
  preview: { value: number; unit: string; cases_measured: number }
}

interface Message {
  role: 'user' | 'assistant'
  text: string
  scope?: string
  engine?: string
  kpi?: KpiProposal
  kpiSaved?: boolean
}

export const CONTEXT_LABELS: Record<string, string> = {
  connections: 'Data connections',
  schedule: 'Schedules',
  data_jobs: 'Data jobs',
  storage: 'Data storage',
  objects_events: 'Objects & events',
  data_model: 'Data model',
  ai_model: 'AI model',
}

const SUGGESTIONS = [
  'What does this data load contain?',
  'Which transformations should I review?',
  'Who created the data model?',
  'Where is the biggest bottleneck?',
]

const DASHBOARD_SUGGESTIONS = [
  'Create a KPI: average days from Goods Receipt to Payment Sent',
  'Create a KPI for rework',
  'Where is the biggest bottleneck?',
  'How much rework do we have?',
]

export default function Copilot(props: {
  context: string | null
  onClearContext: () => void
  page?: string
}) {
  // conversation history persists per workspace so past requests (and the KPIs you
  // asked for) survive navigation and stay browsable by scrolling back
  const historyKey = 'piq_copilot_history:' + (getWorkspaceName() || 'default')
  const [open, setOpen] = useState(true)
  const [messages, setMessages] = useState<Message[]>(() => {
    try {
      const raw = localStorage.getItem(historyKey)
      return raw ? JSON.parse(raw) : []
    } catch {
      return []
    }
  })
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [model, setModel] = useState<{ provider: string; model: string;
                                       active: boolean } | null>(null)
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api<{ provider: string; model: string; active: boolean }>('/api/ai-provider')
      .then(setModel)
      .catch(() => {})
    // land at the latest message when re-opening with existing history
    setTimeout(() => scroller.current?.scrollTo(0, 999999), 30)
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(historyKey, JSON.stringify(messages.slice(-60)))
    } catch {
      // storage full / unavailable — history is best-effort
    }
  }, [messages, historyKey])

  const newChat = () => {
    setMessages([])
    setInput('')
    try {
      localStorage.removeItem(historyKey)
    } catch {
      // ignore
    }
  }

  async function ask(q: string) {
    if (!q.trim() || busy) return
    setMessages((m) => [...m, { role: 'user', text: q }])
    setInput('')
    setBusy(true)
    try {
      const r = await api<{
        answer: string
        scope: string
        engine: string
        kpi_proposal?: KpiProposal
      }>('/api/copilot', {
        method: 'POST',
        body: JSON.stringify({ question: q, context: props.context }),
      })
      setMessages((m) => [
        ...m,
        { role: 'assistant', text: r.answer, scope: r.scope, engine: r.engine,
          kpi: r.kpi_proposal },
      ])
    } catch (e: any) {
      setMessages((m) => [...m, { role: 'assistant', text: `Error: ${e.message}` }])
    } finally {
      setBusy(false)
      setTimeout(() => scroller.current?.scrollTo(0, 999999), 50)
    }
  }

  async function saveKpi(index: number, kpi: KpiProposal) {
    try {
      await api('/api/kpis', {
        method: 'POST',
        body: JSON.stringify({ name: kpi.name, definition: kpi.definition }),
      })
      setMessages((m) =>
        m.map((msg, i) => (i === index ? { ...msg, kpiSaved: true } : msg)),
      )
      window.dispatchEvent(new Event('piq:kpis-changed'))
    } catch (e: any) {
      setMessages((m) => [...m, { role: 'assistant', text: `Could not save KPI: ${e.message}` }])
    }
  }

  if (!open) {
    return (
      <button className="copilot-fab" onClick={() => setOpen(true)} title="Ask co-pilot">
        Copilot
      </button>
    )
  }

  return (
    <aside className="copilot">
      <div className="copilot-head">
        <strong>Copilot</strong>
        <div className="row" style={{ gap: 12 }}>
          {messages.length > 0 && (
            <button className="linklike" onClick={newChat}
                    title="Clear this conversation and start over. Saved KPIs stay on your dashboard.">
              ↺ New chat
            </button>
          )}
          <button className="linklike" onClick={() => setOpen(false)}>
            hide
          </button>
        </div>
      </div>
      {model && (
        <div className="copilot-meta" style={{ padding: '0 14px 6px' }}
             title="Set on the AI Model card in Data load — the same model curates mapping drafts, this chat, and Ask.">
          {model.active
            ? `${PROVIDER_LABEL[model.provider] ?? model.provider} · ${model.model}`
            : 'No AI model configured — rule-based answers only (set one on the AI Model card in Data load)'}
        </div>
      )}

      {props.context && (
        <div className="copilot-context">
          Asking about: <strong>{CONTEXT_LABELS[props.context] ?? props.context}</strong>
          <button className="linklike" onClick={props.onClearContext}>
            ✕ clear
          </button>
        </div>
      )}

      <div className="copilot-messages" ref={scroller}>
        {messages.length === 0 && (
          <div className="copilot-empty">
            Ask about the data load, the AI-built data model, or your process. Click an
            element on the canvas to scope the question to it.
            <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(props.page === 'dashboard' ? DASHBOARD_SUGGESTIONS : SUGGESTIONS).map((s) => (
                <button key={s} className="chip clickable" onClick={() => ask(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={'copilot-msg ' + m.role}>
            <div className="bubble">{m.text}</div>
            {m.kpi && (
              <div className="kpi-proposal">
                <strong>{m.kpi.name}</strong>
                <span>
                  {m.kpi.preview.value} {m.kpi.preview.unit}
                </span>
                {m.kpiSaved ? (
                  <span className="badge high">saved ✓</span>
                ) : (
                  <button className="primary" onClick={() => saveKpi(i, m.kpi!)}>
                    Save KPI
                  </button>
                )}
              </div>
            )}
            {m.role === 'assistant' && m.scope && (
              <div className="copilot-meta">
                scope: {m.scope}
                {m.engine ? ` · ${m.engine}` : ''}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="copilot-meta">thinking…</div>}
      </div>

      <div className="copilot-input">
        <input
          type="text"
          placeholder={props.context ? `Ask about ${CONTEXT_LABELS[props.context] ?? 'selection'}…` : 'Ask anything…'}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && ask(input)}
        />
        <button className="primary" onClick={() => ask(input)} disabled={busy || !input.trim()}>
          →
        </button>
      </div>
    </aside>
  )
}
