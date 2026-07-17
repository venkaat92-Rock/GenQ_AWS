import { useEffect, useRef, useState } from 'react'
// @ts-ignore — bpmn-js ships types under nested paths
import Modeler from 'bpmn-js/lib/Modeler'
import 'bpmn-js/dist/assets/bpmn-js.css'
import 'bpmn-js/dist/assets/diagram-js.css'
import 'bpmn-js/dist/assets/bpmn-font/css/bpmn.css'
import { api } from '../api'

export interface RepoItem {
  id: string
  name: string
  level: string
  parent_id: string | null
  folder_id?: string | null
  status: string
  owner: string
  approver: string | null
  attributes: Record<string, Record<string, string>>
  versions: { file: string; author: string; comment: string; at: string }[]
  latest_file: string
}

interface SelectedElement {
  id: string
  type: string
  name: string
  documentation: string
}

const STATUS_BADGE: Record<string, string> = {
  draft: 'status', in_review: 'medium', approved: 'high', rejected: 'low',
}

interface DictEntry {
  id: string
  category_id: string
  title: string
  description: string
  attributes: Record<string, string>
}
interface DictCategory {
  id: string
  name: string
  parent_id: string | null
}

// which dictionary category feeds suggestions for which BPMN element type
const TYPE_TO_CATEGORY: [RegExp, string][] = [
  [/Task|SubProcess|CallActivity/, 'Activities'],
  [/Event/, 'Events'],
  [/Participant|Lane/, 'Organizational Units'],
  [/DataObject|DataStore/, 'Documents'],
]

function categoryForType(type: string): string | null {
  const hit = TYPE_TO_CATEGORY.find(([re]) => re.test(type))
  return hit ? hit[1] : null
}

export default function BpmnEditor(props: {
  item: RepoItem
  xml: string
  allItems: RepoItem[]
  onSaved: (item: RepoItem) => void
  onClose: () => void
}) {
  const host = useRef<HTMLDivElement>(null)
  const modeler = useRef<any>(null)
  const [selected, setSelected] = useState<SelectedElement | null>(null)
  const [attributes, setAttributes] = useState<Record<string, Record<string, string>>>(
    props.item.attributes ?? {},
  )
  const [attrKey, setAttrKey] = useState('')
  const [attrValue, setAttrValue] = useState('')
  const [author, setAuthor] = useState('')
  const [comment, setComment] = useState('')
  const [approver, setApprover] = useState('')
  const [decisionComment, setDecisionComment] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [item, setItem] = useState(props.item)
  const [dictEntries, setDictEntries] = useState<DictEntry[]>([])
  const [dictCategories, setDictCategories] = useState<DictCategory[]>([])

  useEffect(() => {
    api<{ entries: DictEntry[]; categories: DictCategory[] }>(
      '/api/diagram/dictionary')
      .then((r) => {
        setDictEntries(r.entries)
        setDictCategories(r.categories)
      })
      .catch(() => {})
  }, [])

  // dictionary entries that fit the selected element's type (incl. subcategories)
  const dictFor = (type: string): DictEntry[] => {
    const catName = categoryForType(type)
    if (!catName) return dictEntries
    const root = dictCategories.find((c) => c.name === catName)
    if (!root) return dictEntries
    const ids = new Set([root.id, ...dictCategories
      .filter((c) => c.parent_id === root.id).map((c) => c.id)])
    return dictEntries.filter((e) => ids.has(e.category_id))
  }

  const dictMatch = selected
    ? dictFor(selected.type).find(
        (e) => e.title.toLowerCase() === selected.name.trim().toLowerCase())
    : undefined

  const addToDictionary = async () => {
    if (!selected || !selected.name.trim()) return
    const catName = categoryForType(selected.type) ?? 'Others'
    const cat = dictCategories.find((c) => c.name === catName)
      ?? dictCategories.find((c) => c.name === 'Others')
    if (!cat) return
    try {
      const entry = await api<DictEntry>('/api/diagram/dictionary/entries', {
        method: 'POST',
        body: JSON.stringify({
          category_id: cat.id, title: selected.name.trim(),
          description: selected.documentation,
          attributes: attributes[selected.id] ?? {},
          by: localStorage.getItem('piq_diagram_user') ?? '',
        }),
      })
      setDictEntries([...dictEntries, entry])
      setNotice(`"${entry.title}" added to the dictionary (${catName}).`)
    } catch (e: any) {
      setError(e.message)
    }
  }

  useEffect(() => {
    if (!host.current) return
    const m = new Modeler({ container: host.current })
    modeler.current = m
    m.importXML(props.xml)
      .then(() => (m.get('canvas') as any).zoom('fit-viewport'))
      .catch((e: unknown) => setError('Could not open the diagram: ' + (e as Error).message))
    m.on('selection.changed', (e: any) => {
      const el = e.newSelection[0]
      if (!el || el.type === 'label' || !el.businessObject) {
        setSelected(null)
        return
      }
      const bo = el.businessObject
      setSelected({
        id: el.id,
        type: el.type.replace('bpmn:', ''),
        name: bo.name ?? '',
        documentation: bo.documentation?.[0]?.text ?? '',
      })
    })
    return () => m.destroy()
  }, [])

  const updateElement = (patch: Partial<SelectedElement>) => {
    if (!selected || !modeler.current) return
    const registry = modeler.current.get('elementRegistry')
    const modeling = modeler.current.get('modeling')
    const element = registry.get(selected.id)
    if (!element) return
    if (patch.name !== undefined) {
      modeling.updateProperties(element, { name: patch.name })
    }
    if (patch.documentation !== undefined) {
      const factory = modeler.current.get('bpmnFactory')
      const doc = factory.create('bpmn:Documentation', { text: patch.documentation })
      modeling.updateProperties(element, { documentation: [doc] })
    }
    setSelected({ ...selected, ...patch })
  }

  const elementAttrs = selected ? attributes[selected.id] ?? {} : {}

  const addAttribute = () => {
    if (!selected || !attrKey.trim()) return
    setAttributes({
      ...attributes,
      [selected.id]: { ...elementAttrs, [attrKey.trim()]: attrValue.trim() },
    })
    setAttrKey('')
    setAttrValue('')
  }

  const removeAttribute = (key: string) => {
    if (!selected) return
    const next = { ...elementAttrs }
    delete next[key]
    setAttributes({ ...attributes, [selected.id]: next })
  }

  async function saveVersion() {
    setError('')
    setNotice('')
    try {
      const { xml } = await modeler.current.saveXML({ format: true })
      const saved = await api<RepoItem>(`/api/diagram/repo/${item.id}/versions`, {
        method: 'POST',
        body: JSON.stringify({ xml, author, comment, attributes }),
      })
      setItem(saved)
      setNotice(`Version ${saved.versions.length} saved. Status: draft (re-approval required).`)
      setComment('')
      props.onSaved(saved)
    } catch (e: any) {
      setError(e.message)
    }
  }

  async function submit() {
    setError('')
    try {
      const saved = await api<RepoItem>(`/api/diagram/repo/${item.id}/submit`, {
        method: 'POST',
        body: JSON.stringify({ approver, by: author || item.owner }),
      })
      setItem(saved)
      setNotice(`Submitted for approval to ${saved.approver}.`)
      props.onSaved(saved)
    } catch (e: any) {
      setError(e.message)
    }
  }

  async function decide(action: 'approve' | 'reject') {
    setError('')
    try {
      const saved = await api<RepoItem>(`/api/diagram/repo/${item.id}/decide`, {
        method: 'POST',
        body: JSON.stringify({ action, by: author, comment: decisionComment }),
      })
      setItem(saved)
      setNotice(action === 'approve' ? 'Approved and published.' : 'Rejected — returned to the modeller.')
      props.onSaved(saved)
    } catch (e: any) {
      setError(e.message)
    }
  }

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>
          Editor — {item.name}{' '}
          <span className="badge status">{item.level}</span>{' '}
          <span className={'badge ' + (STATUS_BADGE[item.status] ?? 'status')}>
            {item.status.replace('_', ' ')}
          </span>{' '}
          <span className="hint">v{item.versions.length}</span>
        </h3>
        <button className="linklike" onClick={props.onClose}>Close editor</button>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      <div className="editor-layout">
        <div ref={host} className="bpmn-editor-canvas" />
        <aside className="editor-props">
          <span className="filter-label">Properties</span>
          {selected ? (
            <>
              <p className="hint" style={{ margin: '6px 0' }}>
                {selected.type} · <code className="inline">{selected.id}</code>
              </p>
              <label className="editor-field">
                Name
                <input type="text" value={selected.name} list="dict-suggestions"
                       onChange={(e) => updateElement({ name: e.target.value })} />
                <datalist id="dict-suggestions">
                  {dictFor(selected.type).map((e) => (
                    <option key={e.id} value={e.title}>{e.description}</option>
                  ))}
                </datalist>
              </label>
              {dictMatch ? (
                <p className="hint" style={{ margin: '2px 0 6px' }}>
                  <span className="badge high">dictionary</span>{' '}
                  {dictMatch.description || 'Linked to an approved dictionary term.'}
                  {Object.keys(dictMatch.attributes ?? {}).length > 0 && (
                    <button className="linklike" style={{ marginLeft: 6 }}
                            onClick={() => setAttributes({
                              ...attributes,
                              [selected.id]: { ...dictMatch.attributes,
                                               ...(attributes[selected.id] ?? {}) },
                            })}>
                      apply its attributes
                    </button>
                  )}
                </p>
              ) : (
                selected.name.trim() && (
                  <p className="hint" style={{ margin: '2px 0 6px' }}>
                    Not in the dictionary.{' '}
                    <button className="linklike" onClick={addToDictionary}>
                      Add "{selected.name.trim()}"
                    </button>
                  </p>
                )
              )}
              <label className="editor-field">
                Documentation
                <textarea rows={3} value={selected.documentation}
                          onChange={(e) => updateElement({ documentation: e.target.value })} />
              </label>
              <span className="filter-label" style={{ marginTop: 10 }}>Custom attributes</span>
              {Object.entries(elementAttrs).map(([k, v]) => (
                <div className="row" key={k} style={{ fontSize: 12.5 }}>
                  <code className="inline">{k}</code> {v}
                  <button className="linklike" onClick={() => removeAttribute(k)}>remove</button>
                </div>
              ))}
              <div className="row">
                <input type="text" placeholder="attribute" style={{ maxWidth: 110 }}
                       value={attrKey} onChange={(e) => setAttrKey(e.target.value)} />
                <input type="text" placeholder="value" style={{ maxWidth: 110 }}
                       value={attrValue} onChange={(e) => setAttrValue(e.target.value)} />
                <button className="ghost" onClick={addAttribute} disabled={!attrKey.trim()}>
                  Add
                </button>
              </div>
              <p className="hint" style={{ marginTop: 6 }}>
                Attributes (systems, RACI, risks, KPIs) are stored with the diagram version.
              </p>
            </>
          ) : (
            <p className="hint" style={{ marginTop: 6 }}>
              Select an element on the canvas to edit its name, documentation, and
              custom attributes. Use the palette on the left to add elements.
            </p>
          )}

          <span className="filter-label" style={{ marginTop: 16 }}>Save version</span>
          <input type="text" placeholder="Author" value={author}
                 onChange={(e) => setAuthor(e.target.value)} />
          <input type="text" placeholder="Revision comment" value={comment}
                 onChange={(e) => setComment(e.target.value)} />
          <button className="primary" onClick={saveVersion} disabled={!author.trim()}>
            Save version
          </button>

          {item.status !== 'in_review' ? (
            <>
              <span className="filter-label" style={{ marginTop: 16 }}>Approval</span>
              <input type="text" placeholder="Approver name" value={approver}
                     onChange={(e) => setApprover(e.target.value)} />
              <button className="ghost" onClick={submit}
                      disabled={!approver.trim() || !author.trim()}>
                Submit for approval
              </button>
            </>
          ) : (
            <>
              <span className="filter-label" style={{ marginTop: 16 }}>
                Decision (approver: {item.approver})
              </span>
              <input type="text" placeholder="Decision comment" value={decisionComment}
                     onChange={(e) => setDecisionComment(e.target.value)} />
              <div className="row">
                <button className="primary" onClick={() => decide('approve')}
                        disabled={!author.trim()}>Approve</button>
                <button className="ghost" onClick={() => decide('reject')}
                        disabled={!author.trim()}>Reject</button>
              </div>
              <p className="hint">Enter your name in the Author field to sign the decision.</p>
            </>
          )}
        </aside>
      </div>
    </div>
  )
}
