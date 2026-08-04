import { useEffect, useRef, useState } from 'react'
import { api, getApiKey } from '../api'
import BpmnViewer from '../components/BpmnViewer'
import BpmnEditor, { RepoItem } from '../components/BpmnEditor'
import AiProviderPanel, { AiSettings, describeSource } from '../components/AiProviderPanel'

// starter diagram for new L1-L4 models drawn in the editor
const NEW_DIAGRAM_XML = `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" id="Definitions_1" targetNamespace="http://processiq.local/bpmn">
  <bpmn:process id="Process_1" isExecutable="false">
    <bpmn:startEvent id="StartEvent_1" name="Start" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_1">
      <bpmndi:BPMNShape id="Shape_StartEvent_1" bpmnElement="StartEvent_1">
        <dc:Bounds x="180" y="120" width="36" height="36" />
      </bpmndi:BPMNShape>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>`

const STATUS_BADGE: Record<string, string> = {
  draft: 'status', in_review: 'medium', approved: 'high', rejected: 'low',
}
const LEVEL_HINT: Record<string, string> = {
  L1: 'Process landscape', L2: 'Process group', L3: 'End-to-end process', L4: 'Detailed process',
}

interface Artifact {
  name: string
  kind: 'excel' | 'bpmn' | 'file'
  size_kb: number
  modified: string
}
interface Folder {
  id: string
  name: string
  parent_id: string | null
  access: Record<string, string>
}
interface Member {
  name: string
  role: 'admin' | 'editor' | 'viewer'
}
interface Issue {
  severity: string
  rule_id: string
  category: string
  source?: string
  message: string
  element: string | null
  element_name: string | null
}
interface DiagramSettings {
  provider: 'auto' | 'anthropic' | 'ollama' | 'rule-based'
  has_workspace_key: boolean
  has_server_key: boolean
  ollama_reachable: boolean
}

async function upload(path: string, file: File | null, fields: Record<string, string>) {
  const form = new FormData()
  if (file) form.append('file', file)
  Object.entries(fields).forEach(([k, v]) => form.append(k, v))
  const headers: Record<string, string> = {}
  const key = getApiKey()
  if (key) headers['X-API-Key'] = key
  const response = await fetch(path, { method: 'POST', body: form, headers })
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(data?.detail ?? response.statusText)
  return data
}

const SEVERITY_BADGE: Record<string, string> = {
  error: 'low', warning: 'medium', info: 'status',
}

export default function DiagramStudioPage() {
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [viewing, setViewing] = useState<{ name: string; xml: string } | null>(null)
  const [report, setReport] = useState<{ name: string; issues: Issue[] } | null>(null)
  const [settings, setSettings] = useState<DiagramSettings | null>(null)
  const [sharedAi, setSharedAi] = useState<AiSettings | null>(null)
  const [keyDraft, setKeyDraft] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const notesInput = useRef<HTMLInputElement>(null)
  const excelInput = useRef<HTMLInputElement>(null)
  const bpmnInput = useRef<HTMLInputElement>(null)
  const visioInput = useRef<HTMLInputElement>(null)
  const visioImageInput = useRef<HTMLInputElement>(null)
  const [visioFile, setVisioFile] = useState<File | null>(null)
  const [visioImage, setVisioImage] = useState<File | null>(null)
  const [preserveLayout, setPreserveLayout] = useState(true)
  const [visionResult, setVisionResult] = useState<any>(null)
  const [repo, setRepo] = useState<RepoItem[]>([])
  const [editor, setEditor] = useState<{ item: RepoItem; xml: string } | null>(null)
  const [newName, setNewName] = useState('')
  const [newLevel, setNewLevel] = useState('L3')
  const [newAuthor, setNewAuthor] = useState('')
  const [folders, setFolders] = useState<Folder[]>([])
  const [members, setMembers] = useState<Member[]>([])
  const [membersEnabled, setMembersEnabled] = useState(false)
  const [myRole, setMyRole] = useState<string | null>(null)
  const [curFolder, setCurFolder] = useState<string | null>(null)
  const [workingAs, setWorkingAs] = useState(
    localStorage.getItem('piq_diagram_user') ?? '')
  const [newFolderName, setNewFolderName] = useState('')
  const [accessFolder, setAccessFolder] = useState<Folder | null>(null)
  const [accessDraft, setAccessDraft] = useState<Record<string, string>>({})
  const [showMembers, setShowMembers] = useState(false)

  const refresh = () =>
    Promise.all([
      api<{ artifacts: Artifact[] }>('/api/diagram/artifacts')
        .then((r) => setArtifacts(r.artifacts)),
      api<{ items: RepoItem[]; folders: Folder[]; members_enabled: boolean;
            my_role: string | null }>(
        '/api/diagram/repo' +
        (workingAs.trim() ? `?user=${encodeURIComponent(workingAs.trim())}` : ''),
      ).then((r) => {
        setRepo(r.items)
        setFolders(r.folders ?? [])
        setMembersEnabled(r.members_enabled)
        setMyRole(r.my_role)
      }),
      api<{ members: Member[] }>('/api/diagram/repo/members')
        .then((r) => setMembers(r.members)),
    ]).catch((e) => setError(e.message))

  useEffect(() => {
    localStorage.setItem('piq_diagram_user', workingAs)
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workingAs])

  useEffect(() => {
    api<DiagramSettings>('/api/diagram/settings').then(setSettings).catch(() => {})
  }, [])

  const openEditor = async (item: RepoItem) => {
    const r = await api<{ xml: string }>(
      `/api/diagram/artifacts/${encodeURIComponent(item.latest_file)}/xml`)
    setEditor({ item, xml: r.xml })
    setViewing(null)
  }

  const createDiagram = () =>
    act('new', async () => {
      const item = await api<RepoItem>('/api/diagram/repo/items', {
        method: 'POST',
        body: JSON.stringify({ name: newName.trim(), level: newLevel,
                               xml: NEW_DIAGRAM_XML, author: newAuthor.trim(),
                               folder_id: curFolder }),
      })
      setNewName('')
      setEditor({ item, xml: NEW_DIAGRAM_XML })
    })

  // ---- explorer: folders, members, access ------------------------------------------

  const signer = workingAs.trim() || newAuthor.trim() || 'SME'

  const createFolder = () =>
    act('folder', async () => {
      await api('/api/diagram/repo/folders', {
        method: 'POST',
        body: JSON.stringify({ name: newFolderName.trim(), parent_id: curFolder,
                               by: signer }),
      })
      setNewFolderName('')
    })

  const deleteFolder = (f: Folder) =>
    act(f.id, async () => {
      await api(`/api/diagram/repo/folders/${f.id}?by=${encodeURIComponent(signer)}`,
                { method: 'DELETE' })
      if (curFolder === f.id) setCurFolder(f.parent_id)
    })

  const moveItem = (item: RepoItem, folderId: string) =>
    act(item.id, async () => {
      await api(`/api/diagram/repo/${item.id}`, {
        method: 'PUT',
        body: JSON.stringify(folderId
          ? { folder_id: folderId, by: signer }
          : { clear_folder: true, by: signer }),
      })
    })

  const openAccess = (f: Folder) => {
    setAccessFolder(f)
    setAccessDraft({ ...(f.access ?? {}) })
  }

  const saveAccess = () =>
    act('access', async () => {
      if (!accessFolder) return
      await api(`/api/diagram/repo/folders/${accessFolder.id}/access`, {
        method: 'PUT',
        body: JSON.stringify({ access: accessDraft, by: signer }),
      })
      setAccessFolder(null)
    })

  // explorer contents for the current folder
  const folderById = new Map(folders.map((f) => [f.id, f]))
  const breadcrumb: Folder[] = []
  for (let f = curFolder ? folderById.get(curFolder) : undefined; f;
       f = f.parent_id ? folderById.get(f.parent_id) : undefined) {
    breadcrumb.unshift(f)
  }
  const subfolders = folders
    .filter((f) => f.parent_id === curFolder)
    .sort((a, b) => a.name.localeCompare(b.name))
  const folderItems = repo.filter((i) => (i.folder_id ?? null) === curFolder)
  const canManage = !membersEnabled || myRole === 'admin'

  async function saveSettings(patch: Partial<{ provider: string; anthropic_key: string }>) {
    try {
      const s = await api<DiagramSettings>('/api/diagram/settings', {
        method: 'PUT',
        body: JSON.stringify({ provider: settings?.provider ?? 'auto', ...patch }),
      })
      setSettings(s)
      setKeyDraft('')
      setNotice('AI provider settings saved.')
    } catch (e: any) {
      setError(e.message)
    }
  }

  // The workspace AI (Bedrock / Anthropic / Vertex) powers notes analysis whenever the
  // diagram override is auto|anthropic and no legacy diagram key is set — see
  // api/diagram.py::_workspace_ai. The hint has to account for it, or a perfectly
  // working Bedrock deployment reports "no AI provider reachable".
  const workspaceAiActive = !!sharedAi?.active
  const engineHint = settings
    ? settings.provider === 'rule-based'
      ? '⚠ Rule-based only — no dependencies, systems or gateways will be extracted from notes.'
      : settings.provider === 'ollama'
        ? (settings.ollama_reachable
            ? '✓ Local Ollama available for notes analysis.'
            : '⚠ Ollama selected but not running — notes analysis will fall back to the rule-based parser.')
        : workspaceAiActive
          ? `✓ ${sharedAi!.model} via ${sharedAi!.provider === 'bedrock' ? 'AWS Bedrock'
              : sharedAi!.provider === 'vertex' ? 'Google Vertex AI' : 'the Anthropic API'} — ${describeSource(sharedAi!).toLowerCase()}`
          : settings.has_workspace_key || settings.has_server_key
            ? '✓ Claude AI available for notes analysis.'
            : settings.ollama_reachable
              ? '✓ Local Ollama available for notes analysis.'
              : '⚠ No AI provider configured — notes analysis will fall back to the rule-based parser. Pick one below for desktop-grade results.'
    : ''

  async function act(label: string, fn: () => Promise<void>) {
    setBusy(label)
    setError('')
    setNotice('')
    try {
      await fn()
      await refresh()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }

  const trySample = () =>
    act('sample', async () => {
      const r = await upload('/api/diagram/notes', null, {
        use_sample: 'true',
        process_name: 'Procurement Intake',
      })
      setNotice(
        `Notes analysed with ${r.engine}: ${r.steps} steps across ` +
        `${r.participants.length} participants → ${r.excel}`,
      )
    })

  const onNotes = (f: File) =>
    act('notes', async () => {
      const r = await upload('/api/diagram/notes', f, {
        process_name: f.name.replace(/\.[^.]+$/, ''),
      })
      setNotice(`Notes analysed with ${r.engine}: ${r.steps} steps → ${r.excel}`)
    })

  const onExcelUpload = (f: File) =>
    act('excel', async () => {
      const r = await upload('/api/diagram/excel-to-bpmn', f, {})
      setNotice(`BPMN generated: ${r.bpmn} (${r.issue_count} validation findings)`)
      await view(r.bpmn)
    })

  const onBpmnUpload = (f: File) =>
    act('bpmn', async () => {
      const text = await f.text()
      setViewing({ name: f.name, xml: text })
      const r = await upload('/api/diagram/validate', f, {})
      setReport({ name: f.name, issues: r.issues })
    })

  const toBpmn = (name: string) =>
    act(name, async () => {
      const r = await upload('/api/diagram/excel-to-bpmn', null, { artifact: name })
      setNotice(`BPMN generated: ${r.bpmn} (${r.issue_count} validation findings)`)
      await view(r.bpmn)
    })

  const view = async (name: string) => {
    const r = await api<{ xml: string }>(`/api/diagram/artifacts/${encodeURIComponent(name)}/xml`)
    setViewing({ name, xml: r.xml })
  }

  const validate = (name: string) =>
    act(name, async () => {
      const r = await upload('/api/diagram/validate', null, { artifact: name })
      setReport({ name, issues: r.issues })
    })

  const uplift = (name: string) =>
    act(name, async () => {
      const r = await upload('/api/diagram/uplift', null, { artifact: name })
      setNotice(
        `Uplift complete: ${r.bpmn} — ${r.change_count} structural/naming/routing changes, ` +
        `validation findings ${r.issues_before} → ${r.issues_after}.`,
      )
      await view(r.bpmn)
    })

  const toExcel = (name: string) =>
    act(name, async () => {
      const r = await upload('/api/diagram/bpmn-to-excel', null, { artifact: name })
      setNotice(`Editable Excel review created: ${r.excel} (${r.steps} steps)`)
    })

  // bring a pre-repository artifact under governance, then open it in the editor
  const editArtifact = (name: string) =>
    act(name, async () => {
      const item = await api<RepoItem>('/api/diagram/repo/adopt', {
        method: 'POST',
        body: JSON.stringify({ file: name, folder_id: curFolder,
                               by: workingAs.trim() || 'SME' }),
      })
      await refresh()
      await openEditor(item)
    })

  const convertVisio = (sample = false) =>
    act('visio', async () => {
      const form = new FormData()
      if (!sample && visioFile) form.append('file', visioFile)
      if (visioImage) form.append('image', visioImage)
      form.append('preserve_layout', String(preserveLayout))
      if (sample) form.append('use_sample', 'true')
      const key = getApiKey()
      const response = await fetch('/api/diagram/visio', {
        method: 'POST', body: form, headers: key ? { 'X-API-Key': key } : {},
      })
      const r = await response.json().catch(() => null)
      if (!response.ok) throw new Error(r?.detail ?? response.statusText)
      setNotice(
        `Visio converted: ${r.bpmn} (${r.issue_count} validation findings, ` +
        `${r.preserve_layout ? 'original layout preserved' : 're-gridded layout'})`,
      )
      setVisionResult(r.vision_check)
      setVisioFile(null)
      setVisioImage(null)
      await view(r.bpmn)
    })

  const download = (name: string) => {
    const key = getApiKey()
    fetch(`/api/diagram/artifacts/${encodeURIComponent(name)}`, {
      headers: key ? { 'X-API-Key': key } : {},
    })
      .then((r) => r.blob())
      .then((blob) => {
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = name
        a.click()
        URL.revokeObjectURL(url)
      })
  }

  return (
    <>
      <h1>DiagramIQ studio</h1>
      <p className="lead">
        Notes → Process Discovery Excel → Signavio-ready BPMN 2.0 → validation → uplift.
        Every artifact stays in this workspace; download anything for Signavio, Camunda, or
        bpmn.io.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      <div className="card">
        <div className="row">
          <button className="primary" onClick={trySample} disabled={!!busy}>
            {busy === 'sample' ? 'Analysing…' : 'Try sample meeting notes'}
          </button>
          <button className="ghost" onClick={() => notesInput.current?.click()} disabled={!!busy}>
            Upload notes (.txt/.docx/.pdf)
          </button>
          <button className="ghost" onClick={() => excelInput.current?.click()} disabled={!!busy}>
            Upload discovery Excel
          </button>
          <button className="ghost" onClick={() => bpmnInput.current?.click()} disabled={!!busy}>
            Upload BPMN/XML
          </button>
          <input ref={notesInput} type="file" accept=".txt,.docx,.pdf" hidden
                 onChange={(e) => e.target.files?.[0] && onNotes(e.target.files[0])} />
          <input ref={excelInput} type="file" accept=".xlsx" hidden
                 onChange={(e) => e.target.files?.[0] && onExcelUpload(e.target.files[0])} />
          <input ref={bpmnInput} type="file" accept=".bpmn,.xml" hidden
                 onChange={(e) => e.target.files?.[0] && onBpmnUpload(e.target.files[0])} />
          <button className="ghost" onClick={() => visioInput.current?.click()} disabled={!!busy}>
            Upload Visio (.vsdx)
          </button>
          <input ref={visioInput} type="file" accept=".vsdx,.vsd" hidden
                 onChange={(e) => e.target.files?.[0] && setVisioFile(e.target.files[0])} />
          <input ref={visioImageInput} type="file" accept=".png,.jpg,.jpeg" hidden
                 onChange={(e) => e.target.files?.[0] && setVisioImage(e.target.files[0])} />
          <button className="ghost" style={{ marginLeft: 'auto' }}
                  onClick={() => setShowSettings(!showSettings)}>
            AI provider
          </button>
        </div>

        {visioFile && (
          <div className="filter-panel" style={{ marginTop: 10 }}>
            <div className="filter-group">
              <span className="filter-label">Visio conversion — {visioFile.name}</span>
              <div className="row">
                <label className="row" style={{ gap: 6, fontSize: 13.5 }}>
                  <input type="checkbox" checked={preserveLayout}
                         onChange={(e) => setPreserveLayout(e.target.checked)} />
                  Preserve original Visio layout &amp; swimlanes
                </label>
                <button className="ghost" onClick={() => visioImageInput.current?.click()}>
                  {visioImage ? `${visioImage.name}` : 'Attach screenshot for AI cross-check'}
                </button>
                <button className="primary" onClick={() => convertVisio()} disabled={!!busy}>
                  {busy === 'visio' ? 'Converting…' : 'Convert to BPMN'}
                </button>
                <button className="linklike" onClick={() => { setVisioFile(null); setVisioImage(null) }}>
                  cancel
                </button>
              </div>
              <p className="hint">
                The AI cross-check compares the generated BPMN against your screenshot and
                reports missed or misread shapes (needs a Claude key in AI provider).
                Binary .vsd needs Visio — save as .vsdx first.
              </p>
            </div>
          </div>
        )}
        <p className="hint" style={{ marginTop: 8 }}>{engineHint}</p>

        {showSettings && settings && (
          <>
            <p className="hint" style={{ marginTop: 10 }}>
              One AI backend serves the whole tool — the same model powers ProcessIQ's
              modelling agent, the co-pilot and DiagramIQ's notes analysis. Changing it
              here changes it everywhere.
            </p>
            <AiProviderPanel onLoaded={setSharedAi} />

            <details style={{ marginTop: 12 }}>
              <summary className="hint" style={{ cursor: 'pointer' }}>
                Advanced — DiagramIQ-only override
              </summary>
              <div className="filter-panel" style={{ marginTop: 10 }}>
                <div className="filter-group">
                  <span className="filter-label">Notes-analysis engine</span>
                  <div className="row">
                    <select
                      value={settings.provider}
                      onChange={(e) => saveSettings({ provider: e.target.value })}
                    >
                      <option value="auto">Use the workspace AI model (recommended)</option>
                      <option value="anthropic">Use the workspace AI model — force AI</option>
                      <option value="ollama">Ollama (local, private)</option>
                      <option value="rule-based">Rule-based only (no AI)</option>
                    </select>
                    <span className="chip">Ollama: {settings.ollama_reachable ? 'reachable ✓' : 'not running'}</span>
                  </div>
                  <p className="hint">
                    Leave on <strong>Use the workspace AI model</strong> unless you need a
                    local-only engine. Rule-based extracts steps but not dependencies,
                    systems or decision gateways.
                  </p>
                </div>
                {(settings.has_workspace_key || keyDraft) && (
                  <div className="filter-group">
                    <span className="filter-label">
                      Legacy DiagramIQ-only Anthropic key — overrides the workspace AI above
                    </span>
                    <div className="row">
                      <input
                        type="password"
                        placeholder="sk-ant-…"
                        style={{ maxWidth: 320, border: '1px solid var(--line)', borderRadius: 8, padding: '8px 10px' }}
                        value={keyDraft}
                        onChange={(e) => setKeyDraft(e.target.value)}
                      />
                      <button className="primary" disabled={!keyDraft.trim()}
                              onClick={() => saveSettings({ anthropic_key: keyDraft.trim() })}>
                        Save key
                      </button>
                      {settings.has_workspace_key && (
                        <button className="linklike" onClick={() => saveSettings({ anthropic_key: '' })}>
                          remove key (fall back to the workspace AI)
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </details>
          </>
        )}
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
          <h3 style={{ margin: 0 }}>Process explorer</h3>
          <div className="row">
            <label className="hint" htmlFor="working-as">Working as</label>
            <input id="working-as" type="text" placeholder="Your member name"
                   style={{ maxWidth: 150 }} value={workingAs}
                   onChange={(e) => setWorkingAs(e.target.value)} />
            <button className="linklike" onClick={() => setShowMembers(!showMembers)}>
              Members ({members.length})
            </button>
          </div>
        </div>
        <p className="hint" style={{ marginBottom: 10 }}>
          Folder structure with per-folder access provisioning, as in the Signavio
          explorer. Add members to enable access control — the first member becomes the
          admin, who can then restrict any folder to specific members.
          {membersEnabled && !myRole && (
            <strong> Access control is on: set "Working as" to your member name to see
            governed folders.</strong>
          )}
        </p>

        {showMembers && (
          <div className="subcard" style={{ marginBottom: 12 }}>
            <strong>Workspace members</strong>
            <p className="hint">Members, emails, roles, and licenses are managed on
              the Members page in the sidebar. Folder access below grants per-folder
              rights to these members.</p>
            {members.length === 0 ? (
              <p className="hint">No members yet — access control is off and everyone
                has full rights.</p>
            ) : (
              <table>
                <thead><tr><th>Member</th><th>Role</th></tr></thead>
                <tbody>
                  {members.map((m) => (
                    <tr key={m.name}>
                      <td>{m.name}</td>
                      <td><span className="badge status">{m.role}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 10 }}>
          <div className="row" style={{ gap: 4 }}>
            <button className="linklike" onClick={() => setCurFolder(null)}
                    style={{ fontWeight: curFolder ? 400 : 700 }}>
              Repository
            </button>
            {breadcrumb.map((f) => (
              <span key={f.id}>
                <span className="hint"> / </span>
                <button className="linklike" onClick={() => setCurFolder(f.id)}
                        style={{ fontWeight: f.id === curFolder ? 700 : 400 }}>
                  {f.name}
                </button>
              </span>
            ))}
          </div>
          <div className="row">
            <input type="text" placeholder="New folder" style={{ maxWidth: 130 }}
                   value={newFolderName}
                   onChange={(e) => setNewFolderName(e.target.value)} />
            <button onClick={createFolder} disabled={!!busy || !newFolderName.trim()}>
              Add folder
            </button>
            <input type="text" placeholder="New diagram name" style={{ maxWidth: 150 }}
                   value={newName} onChange={(e) => setNewName(e.target.value)} />
            <select value={newLevel} onChange={(e) => setNewLevel(e.target.value)}>
              {(['L1', 'L2', 'L3', 'L4'] as const).map((l) => (
                <option key={l} value={l}>{l} — {LEVEL_HINT[l]}</option>
              ))}
            </select>
            <input type="text" placeholder="Author" style={{ maxWidth: 110 }}
                   value={newAuthor} onChange={(e) => setNewAuthor(e.target.value)} />
            <button className="primary" onClick={createDiagram}
                    disabled={!!busy || !newName.trim() || !newAuthor.trim()}>
              New diagram
            </button>
          </div>
        </div>

        {accessFolder && (
          <div className="subcard" style={{ marginBottom: 12 }}>
            <strong>Access — {accessFolder.name}</strong>
            <p className="hint">Grant view, edit, or manage per member. Members without
              a grant cannot see this folder or its diagrams. Clear all grants to make
              the folder unrestricted again. Admins always have full access.</p>
            <table>
              <thead><tr><th>Member</th><th>Role</th><th>Access here</th></tr></thead>
              <tbody>
                {members.filter((m) => m.role !== 'admin').map((m) => (
                  <tr key={m.name}>
                    <td>{m.name}</td>
                    <td className="hint">{m.role}</td>
                    <td>
                      <select value={accessDraft[m.name] ?? ''}
                              onChange={(e) => {
                                const next = { ...accessDraft }
                                if (e.target.value) next[m.name] = e.target.value
                                else delete next[m.name]
                                setAccessDraft(next)
                              }}>
                        <option value="">no access</option>
                        <option value="view">view</option>
                        <option value="edit">edit</option>
                        <option value="manage">manage</option>
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="row" style={{ marginTop: 8 }}>
              <button className="primary" onClick={saveAccess} disabled={!!busy}>
                Save access
              </button>
              <button onClick={() => setAccessFolder(null)}>Cancel</button>
            </div>
          </div>
        )}

        {subfolders.length === 0 && folderItems.length === 0 ? (
          <p className="hint">This folder is empty — add a subfolder, create a diagram,
            or convert notes, Excel, or Visio.</p>
        ) : (
          <table>
            <thead>
              <tr><th>Name</th><th>Type</th><th>Status</th><th>Ver.</th><th>Owner</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {subfolders.map((f) => (
                <tr key={f.id}>
                  <td>
                    <button className="linklike" style={{ fontWeight: 650 }}
                            onClick={() => setCurFolder(f.id)}>
                      {f.name}
                    </button>
                  </td>
                  <td><span className="badge status">Folder</span></td>
                  <td>
                    {membersEnabled && Object.keys(f.access ?? {}).length > 0 && (
                      <span className="badge medium">restricted</span>
                    )}
                  </td>
                  <td></td>
                  <td></td>
                  <td className="row" style={{ gap: 8 }}>
                    <button className="linklike" onClick={() => setCurFolder(f.id)}>open</button>
                    {membersEnabled && canManage && (
                      <button className="linklike" onClick={() => openAccess(f)}>access</button>
                    )}
                    <button className="linklike" onClick={() => deleteFolder(f)}>delete</button>
                  </td>
                </tr>
              ))}
              {folderItems.map((item) => (
                <tr key={item.id}>
                  <td>{item.name}</td>
                  <td>
                    <span className="badge status">{item.level}</span>
                    <span className="hint"> {LEVEL_HINT[item.level]}</span>
                  </td>
                  <td>
                    <span className={'badge ' + (STATUS_BADGE[item.status] ?? 'status')}>
                      {item.status.replace('_', ' ')}
                    </span>
                  </td>
                  <td>v{(item as any).version_count ?? item.versions?.length}</td>
                  <td className="hint">{item.owner}</td>
                  <td className="row" style={{ gap: 8 }}>
                    <button className="linklike" onClick={() => openEditor(item)}>edit</button>
                    <button className="linklike" onClick={() => view(item.latest_file)}>view</button>
                    <button className="linklike" onClick={() => validate(item.latest_file)}>validate</button>
                    <button className="linklike" onClick={() => uplift(item.latest_file)}>uplift</button>
                    <button className="linklike" onClick={() => download(item.latest_file)}>download</button>
                    {folders.length > 0 && (
                      <select value={item.folder_id ?? ''} title="Move to folder"
                              onChange={(e) => moveItem(item, e.target.value)}>
                        <option value="">Repository root</option>
                        {folders.map((f) => (
                          <option key={f.id} value={f.id}>{f.name}</option>
                        ))}
                      </select>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {editor && (
        <BpmnEditor
          key={editor.item.id}
          item={editor.item}
          xml={editor.xml}
          allItems={repo}
          onSaved={() => refresh()}
          onClose={() => setEditor(null)}
        />
      )}

      {artifacts.length > 0 && (
        <div className="card">
          <h3>Artifacts ({artifacts.length})</h3>
          <table>
            <thead>
              <tr><th>File</th><th>Kind</th><th>Size</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {artifacts.map((a) => (
                <tr key={a.name}>
                  <td><code className="inline">{a.name}</code></td>
                  <td>{a.kind}</td>
                  <td>{a.size_kb} kb</td>
                  <td className="row" style={{ gap: 6 }}>
                    {a.kind === 'excel' && (
                      <button className="linklike" onClick={() => toBpmn(a.name)}>→ BPMN</button>
                    )}
                    {a.kind === 'bpmn' && (
                      <>
                        <button className="linklike" onClick={() => editArtifact(a.name)}>edit</button>
                        <button className="linklike" onClick={() => view(a.name)}>view</button>
                        <button className="linklike" onClick={() => validate(a.name)}>validate</button>
                        <button className="linklike" onClick={() => uplift(a.name)}>uplift</button>
                        <button className="linklike" onClick={() => toExcel(a.name)}>→ Excel</button>
                      </>
                    )}
                    <button className="linklike" onClick={() => download(a.name)}>download</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {visionResult && (
        <div className="card">
          <h3>AI vision cross-check</h3>
          {visionResult.skipped ? (
            <p className="hint">Skipped: {visionResult.skipped}</p>
          ) : (
            <pre style={{ fontSize: 12.5, whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(visionResult, null, 2)}
            </pre>
          )}
        </div>
      )}

      {viewing && (
        <div className="card">
          <h3>Diagram — {viewing.name}</h3>
          <BpmnViewer xml={viewing.xml} />
          <p className="hint">Scroll to zoom, drag to pan.</p>
        </div>
      )}

      {report && (
        <div className="card">
          <h3>Validation report — {report.name} ({report.issues.length} findings)</h3>
          {report.issues.length === 0 && (
            <p className="hint">No findings — the diagram passes all checks.</p>
          )}
          {report.issues.map((issue, i) => (
            <div className={'insight ' + (issue.severity === 'error' ? 'warn' : '')} key={i}>
              <span className={'badge ' + (SEVERITY_BADGE[issue.severity] ?? 'status')}>
                {issue.severity}
              </span>{' '}
              {issue.source === 'signavio' && <span className="badge status">Signavio</span>}{' '}
              <strong>[{issue.category}]</strong> {issue.message}
              {issue.element_name && <span className="hint"> — {issue.element_name}</span>}
            </div>
          ))}
        </div>
      )}
    </>
  )
}
