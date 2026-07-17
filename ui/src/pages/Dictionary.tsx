import { useEffect, useRef, useState } from 'react'
import { api, getApiKey } from '../api'

export interface DictCategory {
  id: string
  name: string
  color: string
  builtin: boolean
  parent_id: string | null
  count: number
}
export interface DictEntry {
  id: string
  category_id: string
  title: string
  description: string
  attributes: Record<string, string>
  created_by: string
  updated_at: string
  usage_count?: number
}
interface UsageModel {
  model_id: string
  model_name: string
  model_level: string
  model_status: string
  elements: { element_type: string; element_id: string; element_name: string }[]
}

const STATUS_BADGE: Record<string, string> = {
  draft: 'status', in_review: 'medium', approved: 'high', rejected: 'low',
}

const LETTERS = ['All', '0-9', ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')]

export default function DictionaryPage() {
  const [categories, setCategories] = useState<DictCategory[]>([])
  const [entries, setEntries] = useState<DictEntry[]>([])
  const [total, setTotal] = useState(0)
  const [curCat, setCurCat] = useState<string | null>(null)
  const [letter, setLetter] = useState('All')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [editing, setEditing] = useState<DictEntry | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newAttrs, setNewAttrs] = useState<{ k: string; v: string }[]>([])
  const [newCatName, setNewCatName] = useState('')
  const [importMode, setImportMode] = useState('create_only')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [usages, setUsages] = useState<Record<string, UsageModel[]>>({})
  const fileInput = useRef<HTMLInputElement>(null)

  const toggleUsages = (id: string) => {
    if (expanded === id) {
      setExpanded(null)
      return
    }
    setExpanded(id)
    if (!usages[id]) {
      api<{ models: UsageModel[] }>(`/api/diagram/dictionary/entries/${id}/usages`)
        .then((r) => setUsages((u) => ({ ...u, [id]: r.models })))
        .catch(() => setUsages((u) => ({ ...u, [id]: [] })))
    }
  }

  const workingAs = localStorage.getItem('piq_diagram_user') ?? ''

  const refresh = () => {
    const params = new URLSearchParams()
    if (curCat) params.set('category', curCat)
    if (letter !== 'All') params.set('letter', letter)
    if (query.trim()) params.set('q', query.trim())
    return api<{ categories: DictCategory[]; entries: DictEntry[]; total: number }>(
      '/api/diagram/dictionary' + (params.toString() ? `?${params}` : ''),
    )
      .then((r) => {
        setCategories(r.categories)
        setEntries(r.entries)
        setTotal(r.total)
      })
      .catch((e) => setError(e.message))
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curCat, letter, query])

  const run = async (fn: () => Promise<void>, done = '') => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await fn()
      await refresh()
      if (done) setNotice(done)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const createEntry = () =>
    run(async () => {
      await api('/api/diagram/dictionary/entries', {
        method: 'POST',
        body: JSON.stringify({
          category_id: curCat ?? categories.find((c) => c.name === 'Others')?.id,
          title: newTitle.trim(), description: newDesc.trim(),
          attributes: Object.fromEntries(
            newAttrs.filter((a) => a.k.trim()).map((a) => [a.k.trim(), a.v])),
          by: workingAs,
        }),
      })
      setNewTitle('')
      setNewDesc('')
      setNewAttrs([])
      setShowNew(false)
    }, 'Dictionary entry created.')

  const saveEntry = () =>
    run(async () => {
      if (!editing) return
      await api(`/api/diagram/dictionary/entries/${editing.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          title: editing.title, description: editing.description,
          attributes: editing.attributes, by: workingAs,
        }),
      })
      setEditing(null)
    }, 'Entry saved.')

  const deleteSelected = () =>
    run(async () => {
      for (const id of selected) {
        await api(`/api/diagram/dictionary/entries/${id}`, { method: 'DELETE' })
      }
      setSelected([])
    }, 'Deleted.')

  const mergeSelected = () =>
    run(async () => {
      await api('/api/diagram/dictionary/merge', {
        method: 'POST',
        body: JSON.stringify({ ids: selected, by: workingAs }),
      })
      setSelected([])
    }, 'Entries merged — the first selected entry absorbed the rest.')

  const addCategory = () =>
    run(async () => {
      await api('/api/diagram/dictionary/categories', {
        method: 'POST',
        body: JSON.stringify({ name: newCatName.trim(), parent_id: curCat,
                               by: workingAs }),
      })
      setNewCatName('')
    }, 'Category added.')

  const onImport = (f: File) =>
    run(async () => {
      if (!curCat) throw new Error('Open the target category first, then import.')
      const form = new FormData()
      form.append('file', f)
      form.append('category_id', curCat)
      form.append('mode', importMode)
      form.append('by', workingAs)
      const key = getApiKey()
      const resp = await fetch('/api/diagram/dictionary/import', {
        method: 'POST', body: form, headers: key ? { 'X-API-Key': key } : {},
      })
      const r = await resp.json().catch(() => null)
      if (!resp.ok) throw new Error(r?.detail ?? resp.statusText)
      setNotice(`Import finished: ${r.created} created, ${r.updated} updated, `
        + `${r.skipped} skipped${r.errors.length
          ? ` — ${r.errors.length} row error(s): ${r.errors.join('; ')}` : '.'}`)
    })

  const exportExcel = async () => {
    const key = getApiKey()
    const resp = await fetch('/api/diagram/dictionary/export'
      + (curCat ? `?category=${curCat}` : ''),
      { headers: key ? { 'X-API-Key': key } : {} })
    const blob = await resp.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'dictionary.xlsx'
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const roots = categories.filter((c) => !c.parent_id)
  const childrenOf = (id: string) => categories.filter((c) => c.parent_id === id)
  const catName = categories.find((c) => c.id === curCat)?.name

  return (
    <>
      <h1>Dictionary</h1>
      <p className="lead">
        One approved vocabulary for every diagram — organizational units, documents,
        activities, events, IT systems, risks, and controls. The BPMN editor suggests
        these entries while you model, so processes reuse the same terms.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      <div className="dict-layout">
        <div className="dict-tree card">
          <button className={'dict-cat' + (curCat === null ? ' active' : '')}
                  onClick={() => setCurCat(null)}>
            <span className="dict-dot" style={{ background: '#14213d' }} />
            All entries <span className="dict-count">{total}</span>
          </button>
          {roots.map((c) => (
            <div key={c.id}>
              <button className={'dict-cat' + (curCat === c.id ? ' active' : '')}
                      onClick={() => setCurCat(c.id)}>
                <span className="dict-dot" style={{ background: c.color }} />
                {c.name} <span className="dict-count">{c.count}</span>
              </button>
              {childrenOf(c.id).map((sub) => (
                <button key={sub.id}
                        className={'dict-cat sub' + (curCat === sub.id ? ' active' : '')}
                        onClick={() => setCurCat(sub.id)}>
                  <span className="dict-dot" style={{ background: sub.color }} />
                  {sub.name} <span className="dict-count">{sub.count}</span>
                </button>
              ))}
            </div>
          ))}
          <div className="row" style={{ marginTop: 10 }}>
            <input type="text" placeholder={curCat
                     ? `Subcategory of ${catName}` : 'New category'}
                   value={newCatName}
                   onChange={(e) => setNewCatName(e.target.value)} />
            <button onClick={addCategory} disabled={busy || !newCatName.trim()}>
              Add
            </button>
          </div>
        </div>

        <div className="dict-main card">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <div className="row">
              <button className="primary" onClick={() => setShowNew(!showNew)}>
                New entry
              </button>
              <button onClick={deleteSelected}
                      disabled={busy || selected.length === 0}>
                Delete
              </button>
              <button onClick={mergeSelected} disabled={busy || selected.length < 2}
                      title="The first selected entry survives and absorbs the others.">
                Merge dictionary entries
              </button>
            </div>
            <div className="row">
              <select value={importMode}
                      onChange={(e) => setImportMode(e.target.value)}
                      title="What to do when an imported title already exists here">
                <option value="create_only">Import: skip duplicates</option>
                <option value="update_existing">Import: update existing</option>
              </select>
              <button onClick={() => fileInput.current?.click()}
                      disabled={busy || !curCat}
                      title={curCat ? 'Bulk upload .xlsx or .csv into this category'
                                    : 'Open a category first'}>
                Import Excel/CSV
              </button>
              <input ref={fileInput} type="file" accept=".xlsx,.xls,.csv" hidden
                     onChange={(e) => {
                       const f = e.target.files?.[0]
                       if (f) onImport(f)
                       e.target.value = ''
                     }} />
              <button onClick={exportExcel} disabled={busy}>Export Excel</button>
              <button onClick={() => { setUsages({}); setExpanded(null); refresh() }}
                      disabled={busy} title="Reload entries and recompute usages">
                Refresh
              </button>
            </div>
          </div>
          <p className="hint" style={{ margin: '6px 0 8px' }}>
            {curCat ? `Category — ${catName}` : 'All categories'} · bulk upload
            expects a header row; a "title" (or "name") column is required,
            "description" is optional, and every other column becomes a custom
            attribute.
          </p>

          <div className="dict-letters">
            {LETTERS.map((l) => (
              <button key={l} className={letter === l ? 'active' : ''}
                      onClick={() => setLetter(l)}>{l}</button>
            ))}
            <input type="search" placeholder="Search entries" value={query}
                   style={{ marginLeft: 'auto', maxWidth: 200 }}
                   onChange={(e) => setQuery(e.target.value)} />
          </div>

          {showNew && (
            <div className="subcard" style={{ margin: '10px 0' }}>
              <strong>New entry {catName ? `in ${catName}` : '(goes to Others)'}</strong>
              <div className="row" style={{ marginTop: 8, flexWrap: 'wrap' }}>
                <input type="text" placeholder="Title" value={newTitle}
                       onChange={(e) => setNewTitle(e.target.value)} />
                <input type="text" placeholder="Description" style={{ minWidth: 280 }}
                       value={newDesc} onChange={(e) => setNewDesc(e.target.value)} />
              </div>
              {newAttrs.map((a, i) => (
                <div className="row" key={i} style={{ marginTop: 6 }}>
                  <input type="text" placeholder="attribute" value={a.k}
                         onChange={(e) => setNewAttrs(newAttrs.map(
                           (x, j) => j === i ? { ...x, k: e.target.value } : x))} />
                  <input type="text" placeholder="value" value={a.v}
                         onChange={(e) => setNewAttrs(newAttrs.map(
                           (x, j) => j === i ? { ...x, v: e.target.value } : x))} />
                </div>
              ))}
              <div className="row" style={{ marginTop: 8 }}>
                <button className="linklike"
                        onClick={() => setNewAttrs([...newAttrs, { k: '', v: '' }])}>
                  + attribute
                </button>
                <button className="primary" onClick={createEntry}
                        disabled={busy || !newTitle.trim()}>
                  Create
                </button>
                <button onClick={() => setShowNew(false)}>Cancel</button>
              </div>
            </div>
          )}

          {entries.length === 0 ? (
            <p className="hint" style={{ marginTop: 12 }}>No dictionary entries.</p>
          ) : (
            <div className="dict-entries">
              {entries.map((e) => (
                <div key={e.id} className="dict-entry">
                  <input type="checkbox" checked={selected.includes(e.id)}
                         onChange={() => setSelected(selected.includes(e.id)
                           ? selected.filter((x) => x !== e.id)
                           : [...selected, e.id])} />
                  {editing?.id === e.id ? (
                    <div style={{ flex: 1 }}>
                      <input type="text" value={editing.title} style={{ width: '100%' }}
                             onChange={(ev) => setEditing({ ...editing,
                               title: ev.target.value })} />
                      <input type="text" value={editing.description}
                             placeholder="Description"
                             style={{ width: '100%', marginTop: 4 }}
                             onChange={(ev) => setEditing({ ...editing,
                               description: ev.target.value })} />
                      <div className="row" style={{ marginTop: 6 }}>
                        <button className="primary" onClick={saveEntry} disabled={busy}>
                          Save
                        </button>
                        <button onClick={() => setEditing(null)}>Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <div style={{ flex: 1 }}>
                      <strong>{e.title}</strong>
                      {e.description && <div className="hint">{e.description}</div>}
                      {Object.keys(e.attributes ?? {}).length > 0 && (
                        <div style={{ marginTop: 3 }}>
                          {Object.entries(e.attributes).map(([k, v]) => (
                            <span key={k} className="badge status"
                                  style={{ marginRight: 6 }}>{k}: {v}</span>
                          ))}
                        </div>
                      )}
                      {(e.usage_count ?? 0) > 0 ? (
                        <button className="linklike"
                                style={{ marginTop: 5, fontSize: 12, padding: 0 }}
                                onClick={() => toggleUsages(e.id)}>
                          Show usages — used in {e.usage_count} model
                          {e.usage_count === 1 ? '' : 's'}{' '}
                          {expanded === e.id ? '▲' : '▼'}
                        </button>
                      ) : (
                        <div className="hint" style={{ marginTop: 5, fontSize: 12 }}>
                          Not used in any model yet
                        </div>
                      )}
                      {expanded === e.id && (
                        <div className="dict-usages">
                          {!usages[e.id] ? (
                            <span className="hint">Loading usages…</span>
                          ) : usages[e.id].length === 0 ? (
                            <span className="hint">No model references this term.</span>
                          ) : (
                            usages[e.id].map((m) => (
                              <div key={m.model_id} className="dict-usage-model">
                                <strong>{m.model_name}</strong>{' '}
                                <span className="badge status">{m.model_level}</span>{' '}
                                <span className={'badge ' +
                                  (STATUS_BADGE[m.model_status] ?? 'status')}>
                                  {m.model_status.replace('_', ' ')}</span>
                                <div className="hint" style={{ marginTop: 2 }}>
                                  {m.elements.map((el, i) => (
                                    <span key={i} style={{ marginRight: 10 }}>
                                      {el.element_type}: {el.element_name}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            ))
                          )}
                        </div>
                      )}
                    </div>
                  )}
                  <span className="dict-dot" style={{ background:
                    categories.find((c) => c.id === e.category_id)?.color }}
                    title={categories.find((c) => c.id === e.category_id)?.name} />
                  {editing?.id !== e.id && (
                    <button className="linklike" onClick={() => setEditing({ ...e })}>
                      edit
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
