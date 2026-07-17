import { useEffect, useState } from 'react'
import { api } from '../api'
import { RepoItem } from '../components/BpmnEditor'

interface FormField { key: string; label: string; type: string }
interface Condition { var: string; op: string; value: string; next: string }
interface Step {
  id: string
  name: string
  type: string
  assignee?: string
  form?: FormField[]
  next?: string | null
  on_approve?: string | null
  on_reject?: string | null
  conditions?: Condition[]
  default_next?: string | null
}
interface Workflow {
  id: string
  name: string
  description: string
  status: string
  trigger: { type: string; fields: FormField[] }
  roles: string[]
  steps: Step[]
  start_step: string | null
  model_id: string | null
  step_count?: number
  instance_count?: number
  running?: number
}
interface Instance {
  id: string
  workflow_name: string
  status: string
  current_step: string | null
  current_step_name: string | null
  current_step_type: string | null
  current_assignee: string | null
  current_form: FormField[]
  variables: Record<string, string>
  started_by: string
  started_at: string
  history: { step_name: string; action: string; by: string; at: string }[]
}

const STEP_TYPES = [
  { v: 'task', label: 'User task (form)' },
  { v: 'approval', label: 'Approval (approve / reject)' },
  { v: 'notification', label: 'Notification (email)' },
  { v: 'automation', label: 'Automated action' },
  { v: 'condition', label: 'Condition (branch)' },
  { v: 'end', label: 'End' },
]
const OPS = ['==', '!=', '>', '<', 'contains', 'truthy']
const STATUS_BADGE: Record<string, string> = {
  draft: 'status', active: 'high', running: 'medium', completed: 'high',
}
const FIELD_TYPES = ['text', 'number', 'date', 'textarea', 'boolean']
const when = (s: string | null) =>
  s ? s.replace('T', ' ').replace('+00:00', '').slice(0, 16) : '—'
const uid = () => 's' + Math.random().toString(36).slice(2, 7)

export default function WorkflowAutomationPage() {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [models, setModels] = useState<RepoItem[]>([])
  const [sel, setSel] = useState<Workflow | null>(null)
  const [instances, setInstances] = useState<Instance[]>([])
  const [openCase, setOpenCase] = useState<Instance | null>(null)
  const [triggerData, setTriggerData] = useState<Record<string, string>>({})
  const [stepData, setStepData] = useState<Record<string, string>>({})
  const [fromModel, setFromModel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const by = localStorage.getItem('piq_diagram_user') ?? ''

  const loadList = () =>
    api<{ workflows: Workflow[] }>('/api/diagram/workflows')
      .then((r) => setWorkflows(r.workflows)).catch((e) => setError(e.message))

  useEffect(() => {
    loadList()
    api<{ items: RepoItem[] }>(
      '/api/diagram/repo' + (by.trim() ? `?user=${encodeURIComponent(by.trim())}` : ''))
      .then((r) => setModels(r.items)).catch(() => {})
  }, [])

  const run = async (fn: () => Promise<void>, done = '') => {
    setBusy(true); setError(''); setNotice('')
    try { await fn(); if (done) setNotice(done) }
    catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  const openWf = (id: string) =>
    run(async () => {
      const wf = await api<Workflow>(`/api/diagram/workflows/${id}`)
      setSel(wf)
      setTriggerData({})
      const r = await api<{ instances: Instance[] }>(`/api/diagram/workflows/${id}/instances`)
      setInstances(r.instances)
      setOpenCase(null)
    })

  const newBlank = () =>
    run(async () => {
      const wf = await api<Workflow>('/api/diagram/workflows', {
        method: 'POST',
        body: JSON.stringify({ name: 'New workflow', steps: [
          { id: 's1', name: 'First task', type: 'task', assignee: '', form: [],
            next: 'end' }], start_step: 's1', by }),
      })
      await loadList()
      await openWf(wf.id)
    }, 'Workflow created.')

  const genFromModel = () =>
    run(async () => {
      if (!fromModel) throw new Error('Pick a model first.')
      const wf = await api<Workflow>(`/api/diagram/workflows/from-model/${fromModel}`, {
        method: 'POST', body: JSON.stringify({ by }),
      })
      setFromModel('')
      await loadList()
      await openWf(wf.id)
    }, 'Workflow generated from model.')

  const patch = (changes: Partial<Workflow>) =>
    setSel((w) => (w ? { ...w, ...changes } : w))
  const patchStep = (i: number, changes: Partial<Step>) =>
    setSel((w) => w ? { ...w, steps: w.steps.map((s, j) =>
      j === i ? { ...s, ...changes } : s) } : w)

  const addStep = () => setSel((w) => w ? { ...w, steps: [...w.steps,
    { id: uid(), name: 'New step', type: 'task', assignee: '', form: [], next: 'end' }] } : w)
  const removeStep = (i: number) => setSel((w) => w
    ? { ...w, steps: w.steps.filter((_, j) => j !== i) } : w)
  const moveStep = (i: number, d: number) => setSel((w) => {
    if (!w) return w
    const steps = [...w.steps]; const j = i + d
    if (j < 0 || j >= steps.length) return w
    ;[steps[i], steps[j]] = [steps[j], steps[i]]
    return { ...w, steps }
  })

  const targetOptions = (steps: Step[]) =>
    [{ id: 'end', name: 'End' }, ...steps.map((s) => ({ id: s.id, name: s.name }))]

  const save = () =>
    run(async () => {
      if (!sel) return
      const wf = await api<Workflow>(`/api/diagram/workflows/${sel.id}`, {
        method: 'PUT',
        body: JSON.stringify({ name: sel.name, description: sel.description,
          trigger: sel.trigger, roles: sel.roles, steps: sel.steps,
          start_step: sel.start_step, by }),
      })
      setSel(wf); await loadList()
    }, 'Saved (draft — activate to run new cases).')

  const activate = () =>
    run(async () => {
      if (!sel) return
      const wf = await api<Workflow>(`/api/diagram/workflows/${sel.id}/activate`,
        { method: 'POST' })
      setSel(wf); await loadList()
    }, 'Workflow activated.')

  const del = () =>
    run(async () => {
      if (!sel) return
      await api(`/api/diagram/workflows/${sel.id}`, { method: 'DELETE' })
      setSel(null); await loadList()
    }, 'Workflow deleted.')

  const startCase = () =>
    run(async () => {
      if (!sel) return
      const inst = await api<Instance>(`/api/diagram/workflows/${sel.id}/start`, {
        method: 'POST', body: JSON.stringify({ variables: triggerData, by }),
      })
      setTriggerData({})
      const r = await api<{ instances: Instance[] }>(`/api/diagram/workflows/${sel.id}/instances`)
      setInstances(r.instances)
      setOpenCase(inst)
      setStepData({})
      await loadList()
    }, 'Case started.')

  const completeStep = (decision?: string) =>
    run(async () => {
      if (!openCase) return
      const inst = await api<Instance>(
        `/api/diagram/workflows/instances/${openCase.id}/complete`, {
        method: 'POST', body: JSON.stringify({ data: stepData, decision, by }),
      })
      setOpenCase(inst); setStepData({})
      if (sel) {
        const r = await api<{ instances: Instance[] }>(`/api/diagram/workflows/${sel.id}/instances`)
        setInstances(r.instances)
      }
      await loadList()
    })

  const field = (f: FormField, val: string, set: (v: string) => void) => (
    <label key={f.key} className="editor-field">
      {f.label || f.key}
      {f.type === 'textarea'
        ? <textarea rows={2} value={val} onChange={(e) => set(e.target.value)} />
        : <input type={f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : 'text'}
                 value={val} onChange={(e) => set(e.target.value)} />}
    </label>
  )

  return (
    <>
      <h1>Workflow automation</h1>
      <p className="lead">
        Build executable workflows — a trigger, then user tasks, approvals, notifications
        and conditional routing — and run them as cases. The DiagramIQ equivalent of SAP
        Signavio Process Governance; generate one from any repository model to start.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <h3 style={{ margin: 0 }}>Workflows ({workflows.length})</h3>
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <button className="primary" onClick={newBlank} disabled={busy}>New workflow</button>
            <select value={fromModel} onChange={(e) => setFromModel(e.target.value)}>
              <option value="">Generate from model…</option>
              {models.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.level})</option>)}
            </select>
            <button onClick={genFromModel} disabled={busy || !fromModel}>Generate</button>
          </div>
        </div>
        {workflows.length === 0 ? (
          <p className="hint">No workflows yet — create one or generate from a model.</p>
        ) : (
          <table>
            <thead><tr><th>Workflow</th><th>Status</th><th>Steps</th><th>Cases</th><th></th></tr></thead>
            <tbody>
              {workflows.map((w) => (
                <tr key={w.id}>
                  <td>{w.name}</td>
                  <td><span className={'badge ' + (STATUS_BADGE[w.status] ?? 'status')}>
                    {w.status}</span></td>
                  <td>{w.step_count}</td>
                  <td>{w.instance_count}{(w.running ?? 0) > 0 &&
                    <span className="badge medium" style={{ marginLeft: 6 }}>
                      {w.running} running</span>}</td>
                  <td><button className="linklike" onClick={() => openWf(w.id)}>open</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {sel && (
        <div className="card">
          <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <h3 style={{ margin: 0 }}>
              Edit — {sel.name}{' '}
              <span className={'badge ' + (STATUS_BADGE[sel.status] ?? 'status')}>
                {sel.status}</span>
            </h3>
            <div className="row">
              <button className="primary" onClick={save} disabled={busy}>Save</button>
              <button onClick={activate} disabled={busy || sel.status === 'active'}>
                Activate</button>
              <button onClick={del} disabled={busy}>Delete</button>
              <button className="linklike" onClick={() => setSel(null)}>Close</button>
            </div>
          </div>

          <div className="row" style={{ flexWrap: 'wrap', marginTop: 8 }}>
            <label className="editor-field" style={{ flex: 1, minWidth: 200 }}>Name
              <input type="text" value={sel.name}
                     onChange={(e) => patch({ name: e.target.value })} /></label>
            <label className="editor-field" style={{ flex: 2, minWidth: 260 }}>Description
              <input type="text" value={sel.description}
                     onChange={(e) => patch({ description: e.target.value })} /></label>
            <label className="editor-field">Starts at
              <select value={sel.start_step ?? ''}
                      onChange={(e) => patch({ start_step: e.target.value })}>
                {sel.steps.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select></label>
          </div>

          <div className="subcard" style={{ marginTop: 10 }}>
            <strong>Trigger</strong>
            <div className="row" style={{ marginTop: 6 }}>
              <label className="editor-field">How it starts
                <select value={sel.trigger?.type ?? 'manual'}
                        onChange={(e) => patch({ trigger: { ...sel.trigger,
                          type: e.target.value } })}>
                  <option value="manual">Manual start</option>
                  <option value="form">Start form</option>
                </select></label>
            </div>
            {sel.trigger?.type === 'form' && (
              <FieldEditor
                fields={sel.trigger.fields ?? []}
                onChange={(fields) => patch({ trigger: { ...sel.trigger, fields } })} />
            )}
          </div>

          <h4 style={{ margin: '14px 0 6px' }}>Steps</h4>
          {sel.steps.map((s, i) => (
            <div key={s.id} className="subcard" style={{ marginBottom: 10 }}>
              <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
                <div className="row" style={{ flexWrap: 'wrap' }}>
                  <span className="badge status">{i + 1}</span>
                  <input type="text" value={s.name} style={{ maxWidth: 200 }}
                         onChange={(e) => patchStep(i, { name: e.target.value })} />
                  <select value={s.type} onChange={(e) => patchStep(i, { type: e.target.value })}>
                    {STEP_TYPES.map((t) => <option key={t.v} value={t.v}>{t.label}</option>)}
                  </select>
                  {s.type !== 'end' && s.type !== 'condition' && (
                    <input type="text" placeholder="Assignee / role" style={{ maxWidth: 150 }}
                           value={s.assignee ?? ''}
                           onChange={(e) => patchStep(i, { assignee: e.target.value })} />
                  )}
                </div>
                <div className="row">
                  <button className="linklike" onClick={() => moveStep(i, -1)}>↑</button>
                  <button className="linklike" onClick={() => moveStep(i, 1)}>↓</button>
                  <button className="linklike" onClick={() => removeStep(i)}>remove</button>
                </div>
              </div>

              {(s.type === 'task' || s.type === 'notification' || s.type === 'automation') && (
                <div className="row" style={{ marginTop: 6 }}>
                  <label className="editor-field">Next step
                    <select value={s.next ?? 'end'}
                            onChange={(e) => patchStep(i, { next: e.target.value })}>
                      {targetOptions(sel.steps).map((o) =>
                        <option key={o.id} value={o.id}>{o.name}</option>)}
                    </select></label>
                </div>
              )}
              {s.type === 'task' && (
                <div style={{ marginTop: 6 }}>
                  <span className="hint">Form fields</span>
                  <FieldEditor fields={s.form ?? []}
                               onChange={(form) => patchStep(i, { form })} />
                </div>
              )}
              {s.type === 'approval' && (
                <div className="row" style={{ marginTop: 6 }}>
                  <label className="editor-field">On approve →
                    <select value={s.on_approve ?? 'end'}
                            onChange={(e) => patchStep(i, { on_approve: e.target.value })}>
                      {targetOptions(sel.steps).map((o) =>
                        <option key={o.id} value={o.id}>{o.name}</option>)}
                    </select></label>
                  <label className="editor-field">On reject →
                    <select value={s.on_reject ?? 'end'}
                            onChange={(e) => patchStep(i, { on_reject: e.target.value })}>
                      {targetOptions(sel.steps).map((o) =>
                        <option key={o.id} value={o.id}>{o.name}</option>)}
                    </select></label>
                </div>
              )}
              {s.type === 'condition' && (
                <ConditionEditor step={s} targets={targetOptions(sel.steps)}
                                 onChange={(c) => patchStep(i, c)} />
              )}
            </div>
          ))}
          <button onClick={addStep}>+ Add step</button>
        </div>
      )}

      {sel && sel.status === 'active' && (
        <div className="card">
          <h3>Run a case</h3>
          {sel.trigger?.type === 'form' && (sel.trigger.fields ?? []).length > 0 && (
            <div className="row" style={{ flexWrap: 'wrap' }}>
              {(sel.trigger.fields ?? []).map((f) =>
                field(f, triggerData[f.key] ?? '',
                      (v) => setTriggerData({ ...triggerData, [f.key]: v })))}
            </div>
          )}
          <button className="primary" onClick={startCase} disabled={busy}
                  style={{ marginTop: 8 }}>Start case</button>
        </div>
      )}

      {sel && instances.length > 0 && (
        <div className="card">
          <h3>Cases ({instances.length})</h3>
          <table>
            <thead><tr><th>Case</th><th>Status</th><th>Current step</th>
              <th>Started by</th><th>When</th><th></th></tr></thead>
            <tbody>
              {instances.map((i) => (
                <tr key={i.id}>
                  <td className="hint">{i.id.slice(0, 10)}</td>
                  <td><span className={'badge ' + (STATUS_BADGE[i.status] ?? 'status')}>
                    {i.status}</span></td>
                  <td>{i.current_step_name ?? '—'}</td>
                  <td>{i.started_by}</td>
                  <td className="hint">{when(i.started_at)}</td>
                  <td><button className="linklike"
                    onClick={() => { setOpenCase(i); setStepData({}) }}>open</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {openCase && (
        <div className="card">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <h3 style={{ margin: 0 }}>Case · {openCase.workflow_name}{' '}
              <span className={'badge ' + (STATUS_BADGE[openCase.status] ?? 'status')}>
                {openCase.status}</span></h3>
            <button className="linklike" onClick={() => setOpenCase(null)}>Close</button>
          </div>

          {openCase.status === 'running' && openCase.current_step_name && (
            <div className="subcard" style={{ marginTop: 8 }}>
              <strong>Current step — {openCase.current_step_name}</strong>{' '}
              <span className="hint">({openCase.current_step_type}
                {openCase.current_assignee ? ` · ${openCase.current_assignee}` : ''})</span>
              {(openCase.current_form ?? []).length > 0 && (
                <div className="row" style={{ flexWrap: 'wrap', marginTop: 6 }}>
                  {(openCase.current_form ?? []).map((f) =>
                    field(f, stepData[f.key] ?? '',
                          (v) => setStepData({ ...stepData, [f.key]: v })))}
                </div>
              )}
              <div className="row" style={{ marginTop: 8 }}>
                {openCase.current_step_type === 'approval' ? (
                  <>
                    <button className="primary" onClick={() => completeStep('approve')}
                            disabled={busy}>Approve</button>
                    <button onClick={() => completeStep('reject')} disabled={busy}>Reject</button>
                  </>
                ) : (
                  <button className="primary" onClick={() => completeStep()} disabled={busy}>
                    Complete step</button>
                )}
              </div>
            </div>
          )}

          <h4 style={{ margin: '12px 0 6px' }}>History</h4>
          <table>
            <thead><tr><th>When</th><th>Step</th><th>Action</th><th>By</th></tr></thead>
            <tbody>
              {openCase.history.map((h, i) => (
                <tr key={i}>
                  <td className="hint">{when(h.at)}</td>
                  <td>{h.step_name}</td>
                  <td><span className="badge status">{h.action}</span></td>
                  <td>{h.by}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {Object.keys(openCase.variables ?? {}).length > 0 && (
            <p className="hint" style={{ marginTop: 8 }}>
              Data: {Object.entries(openCase.variables).map(([k, v]) => `${k}=${v}`).join(', ')}
            </p>
          )}
        </div>
      )}
    </>
  )
}

function FieldEditor(props: { fields: FormField[]; onChange: (f: FormField[]) => void }) {
  const set = (i: number, c: Partial<FormField>) =>
    props.onChange(props.fields.map((f, j) => (j === i ? { ...f, ...c } : f)))
  return (
    <div style={{ marginTop: 4 }}>
      {props.fields.map((f, i) => (
        <div key={i} className="row" style={{ marginBottom: 4 }}>
          <input type="text" placeholder="key" style={{ maxWidth: 110 }} value={f.key}
                 onChange={(e) => set(i, { key: e.target.value })} />
          <input type="text" placeholder="label" style={{ maxWidth: 150 }} value={f.label}
                 onChange={(e) => set(i, { label: e.target.value })} />
          <select value={f.type} onChange={(e) => set(i, { type: e.target.value })}>
            {FIELD_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <button className="linklike"
                  onClick={() => props.onChange(props.fields.filter((_, j) => j !== i))}>
            remove</button>
        </div>
      ))}
      <button className="linklike" onClick={() => props.onChange([...props.fields,
        { key: '', label: '', type: 'text' }])}>+ field</button>
    </div>
  )
}

function ConditionEditor(props: {
  step: Step; targets: { id: string; name: string }[]
  onChange: (c: Partial<Step>) => void
}) {
  const conds = props.step.conditions ?? []
  const set = (i: number, c: Partial<Condition>) =>
    props.onChange({ conditions: conds.map((x, j) => (j === i ? { ...x, ...c } : x)) })
  return (
    <div style={{ marginTop: 6 }}>
      <span className="hint">If a variable matches, route to a step; else the default.</span>
      {conds.map((c, i) => (
        <div key={i} className="row" style={{ marginTop: 4, flexWrap: 'wrap' }}>
          <input type="text" placeholder="variable" style={{ maxWidth: 110 }} value={c.var}
                 onChange={(e) => set(i, { var: e.target.value })} />
          <select value={c.op} onChange={(e) => set(i, { op: e.target.value })}>
            {OPS.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <input type="text" placeholder="value" style={{ maxWidth: 90 }} value={c.value}
                 onChange={(e) => set(i, { value: e.target.value })} />
          <span className="hint">→</span>
          <select value={c.next} onChange={(e) => set(i, { next: e.target.value })}>
            {props.targets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
          <button className="linklike"
                  onClick={() => props.onChange({
                    conditions: conds.filter((_, j) => j !== i) })}>remove</button>
        </div>
      ))}
      <div className="row" style={{ marginTop: 4 }}>
        <button className="linklike" onClick={() => props.onChange({
          conditions: [...conds, { var: '', op: '==', value: '',
            next: props.targets[0]?.id ?? 'end' }] })}>+ condition</button>
        <label className="editor-field">Default →
          <select value={props.step.default_next ?? 'end'}
                  onChange={(e) => props.onChange({ default_next: e.target.value })}>
            {props.targets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select></label>
      </div>
    </div>
  )
}
