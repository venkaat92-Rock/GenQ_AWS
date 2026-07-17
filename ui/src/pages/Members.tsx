import { useEffect, useState } from 'react'
import { api } from '../api'

export interface Member {
  id: string
  name: string
  email: string
  role: 'admin' | 'editor' | 'viewer'
  licenses: string[]
  added_by: string
  added_at: string
}
interface AuditEntry {
  action: string
  member: string
  by: string
  at: string
  detail: string
}

const LICENSE_LABEL: Record<string, string> = {
  processiq: 'ProcessIQ', diagramiq: 'DiagramIQ',
}
const ROLE_HINT: Record<string, string> = {
  admin: 'Manages members, licenses, and folder access. Full rights everywhere.',
  editor: 'Creates and edits content in the offerings their license covers.',
  viewer: 'Read-only access in the offerings their license covers.',
}

export default function MembersPage() {
  const [members, setMembers] = useState<Member[]>([])
  const [audit, setAudit] = useState<AuditEntry[]>([])
  const [enabled, setEnabled] = useState(false)
  const [workingAs, setWorkingAs] = useState(
    localStorage.getItem('piq_diagram_user') ?? '')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('editor')
  const [licenses, setLicenses] = useState<string[]>(['processiq', 'diagramiq'])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const me = members.find(
    (m) => m.name.toLowerCase() === workingAs.trim().toLowerCase()
        || m.email.toLowerCase() === workingAs.trim().toLowerCase())
  const isAdmin = !enabled || me?.role === 'admin'

  const refresh = () =>
    api<{ members: Member[]; audit: AuditEntry[]; enabled: boolean }>('/api/members')
      .then((r) => {
        setMembers(r.members)
        setAudit(r.audit)
        setEnabled(r.enabled)
      })
      .catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  useEffect(() => {
    localStorage.setItem('piq_diagram_user', workingAs)
  }, [workingAs])

  const run = async (fn: () => Promise<void>, done: string) => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await fn()
      await refresh()
      setNotice(done)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const addMember = () =>
    run(async () => {
      await api('/api/members', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim(), email: email.trim(), role,
                               licenses, by: workingAs.trim() }),
      })
      setName('')
      setEmail('')
    }, 'Member added.')

  const update = (m: Member, patch: Record<string, unknown>) =>
    run(async () => {
      await api(`/api/members/${m.id}`, {
        method: 'PUT',
        body: JSON.stringify({ ...patch, by: workingAs.trim() }),
      })
    }, 'Member updated.')

  const remove = (m: Member) =>
    run(async () => {
      await api(`/api/members/${m.id}?by=${encodeURIComponent(workingAs.trim())}`,
                { method: 'DELETE' })
    }, 'Member removed.')

  const toggleLicense = (m: Member, license: string) => {
    const next = m.licenses.includes(license)
      ? m.licenses.filter((l) => l !== license)
      : [...m.licenses, license]
    update(m, { licenses: next })
  }

  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1>Members</h1>
          <p className="lead">
            One member directory for ProcessIQ and DiagramIQ. Admins add members by
            email, assign a role, and license the offerings each member may use.
          </p>
        </div>
        <div className="row">
          <label className="hint" htmlFor="me">Working as</label>
          <input id="me" type="text" placeholder="Your name or email"
                 style={{ maxWidth: 180 }} value={workingAs}
                 onChange={(e) => setWorkingAs(e.target.value)} />
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      {!enabled && (
        <p className="hint">
          No members yet, so access control is off and everyone has full rights.
          Add the first member — it must be an admin — to switch governance on.
        </p>
      )}
      {enabled && !me && (
        <p className="hint">
          Set "Working as" to your member name or email. Member management needs an
          admin identity; DiagramIQ folder visibility also follows it.
        </p>
      )}

      {isAdmin && (
        <div className="card">
          <h3>Add member</h3>
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <input type="text" placeholder="Full name" style={{ maxWidth: 170 }}
                   value={name} onChange={(e) => setName(e.target.value)} />
            <input type="email" placeholder="email@company.com"
                   style={{ maxWidth: 210 }}
                   value={email} onChange={(e) => setEmail(e.target.value)} />
            <select value={role} onChange={(e) => setRole(e.target.value)}
                    title={ROLE_HINT[role]}>
              <option value="admin">admin</option>
              <option value="editor">editor</option>
              <option value="viewer">viewer</option>
            </select>
            {Object.entries(LICENSE_LABEL).map(([key, label]) => (
              <label key={key} className="row" style={{ gap: 5, fontSize: 13 }}>
                <input type="checkbox" checked={licenses.includes(key)}
                       onChange={() =>
                         setLicenses(licenses.includes(key)
                           ? licenses.filter((l) => l !== key)
                           : [...licenses, key])} />
                {label}
              </label>
            ))}
            <button className="primary" onClick={addMember}
                    disabled={busy || !name.trim() || !email.trim()
                              || licenses.length === 0}>
              Add member
            </button>
          </div>
          <p className="hint" style={{ marginTop: 6 }}>{ROLE_HINT[role]}</p>
        </div>
      )}

      <div className="card">
        <h3>Directory ({members.length})</h3>
        {members.length === 0 ? (
          <p className="hint">Empty. The first member must be an admin.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Member</th><th>Email</th><th>Role</th><th>Licenses</th>
                <th>Added</th>{isAdmin && <th></th>}
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id}>
                  <td>{m.name}</td>
                  <td className={m.email ? '' : 'hint'}>
                    {m.email || 'no email — edit to add'}
                  </td>
                  <td>
                    {isAdmin ? (
                      <select value={m.role}
                              onChange={(e) => update(m, { role: e.target.value })}>
                        <option value="admin">admin</option>
                        <option value="editor">editor</option>
                        <option value="viewer">viewer</option>
                      </select>
                    ) : (
                      <span className="badge status">{m.role}</span>
                    )}
                  </td>
                  <td>
                    {Object.entries(LICENSE_LABEL).map(([key, label]) => (
                      isAdmin ? (
                        <label key={key} className="row"
                               style={{ gap: 4, fontSize: 12.5, display: 'inline-flex',
                                        marginRight: 10 }}>
                          <input type="checkbox" checked={m.licenses.includes(key)}
                                 onChange={() => toggleLicense(m, key)} />
                          {label}
                        </label>
                      ) : (
                        m.licenses.includes(key) && (
                          <span key={key} className="badge status"
                                style={{ marginRight: 6 }}>{label}</span>
                        )
                      )
                    ))}
                  </td>
                  <td className="hint">{m.added_by}</td>
                  {isAdmin && (
                    <td>
                      <button className="linklike" onClick={() => remove(m)}>
                        remove
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="hint" style={{ marginTop: 8 }}>
          Licenses gate the offerings: a member without the DiagramIQ license cannot
          see or touch the process repository; folder-level restrictions come on top
          (provisioned per folder in the DiagramIQ explorer). The last admin can
          never be removed or demoted.
        </p>
      </div>

      {audit.length > 0 && (
        <div className="card">
          <h3>Recent changes</h3>
          <table>
            <thead>
              <tr><th>When</th><th>Action</th><th>Member</th><th>By</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {[...audit].reverse().map((a, i) => (
                <tr key={i}>
                  <td className="hint">{a.at.replace('T', ' ').replace('+00:00', ' UTC')}</td>
                  <td><span className="badge status">{a.action}</span></td>
                  <td>{a.member}</td>
                  <td>{a.by}</td>
                  <td className="hint">{a.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
