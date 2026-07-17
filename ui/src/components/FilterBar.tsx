import { useEffect, useState } from 'react'
import { api } from '../api'

export interface FilterState {
  vendors: string[]
  startFrom: string
  startTo: string
  minDays: string
  maxDays: string
  includeActivity: string
  excludeActivity: string
  onlyRework: boolean
}

export const EMPTY_FILTERS: FilterState = {
  vendors: [],
  startFrom: '',
  startTo: '',
  minDays: '',
  maxDays: '',
  includeActivity: '',
  excludeActivity: '',
  onlyRework: false,
}

export function toQuery(f: FilterState): string {
  const params = new URLSearchParams()
  if (f.vendors.length) params.set('vendors', f.vendors.join(','))
  if (f.startFrom) params.set('start_from', f.startFrom)
  if (f.startTo) params.set('start_to', f.startTo)
  if (f.minDays) params.set('min_days', f.minDays)
  if (f.maxDays) params.set('max_days', f.maxDays)
  if (f.includeActivity) params.set('include_activity', f.includeActivity)
  if (f.excludeActivity) params.set('exclude_activity', f.excludeActivity)
  if (f.onlyRework) params.set('only_rework', 'true')
  const s = params.toString()
  return s ? `?${s}` : ''
}

interface Facets {
  party: string
  vendors: string[]
  activities: string[]
  date_min: string
  date_max: string
  duration_max_days: number
}

export default function FilterBar(props: {
  value: FilterState
  onChange: (f: FilterState) => void
}) {
  const { value, onChange } = props
  const [facets, setFacets] = useState<Facets | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    api<Facets>('/api/analysis/facets').then(setFacets).catch(() => {})
  }, [])

  const activeCount =
    value.vendors.length +
    (value.startFrom ? 1 : 0) +
    (value.startTo ? 1 : 0) +
    (value.minDays ? 1 : 0) +
    (value.maxDays ? 1 : 0) +
    (value.includeActivity ? 1 : 0) +
    (value.excludeActivity ? 1 : 0) +
    (value.onlyRework ? 1 : 0)

  const set = (patch: Partial<FilterState>) => onChange({ ...value, ...patch })

  return (
    <div className="filterbar">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <button className="ghost" onClick={() => setOpen(!open)}>
          Filters{activeCount > 0 ? ` (${activeCount} active)` : ''} {open ? '▴' : '▾'}
        </button>
        {activeCount > 0 && (
          <button className="linklike" onClick={() => onChange(EMPTY_FILTERS)}>
            clear all
          </button>
        )}
      </div>

      {open && facets && (
        <div className="filter-panel">
          <div className="filter-group">
            <span className="filter-label">{facets.party ?? 'Vendor'}</span>
            <div className="row">
              {facets.vendors.map((v) => (
                <button
                  key={v}
                  className={'chip clickable' + (value.vendors.includes(v) ? ' on' : '')}
                  onClick={() =>
                    set({
                      vendors: value.vendors.includes(v)
                        ? value.vendors.filter((x) => x !== v)
                        : [...value.vendors, v],
                    })
                  }
                >
                  {v}
                </button>
              ))}
            </div>
          </div>

          <div className="filter-group">
            <span className="filter-label">
              Case started between ({facets.date_min} … {facets.date_max})
            </span>
            <div className="row">
              <input
                type="date"
                value={value.startFrom}
                min={facets.date_min}
                max={facets.date_max}
                onChange={(e) => set({ startFrom: e.target.value })}
              />
              <span className="hint">to</span>
              <input
                type="date"
                value={value.startTo}
                min={facets.date_min}
                max={facets.date_max}
                onChange={(e) => set({ startTo: e.target.value })}
              />
            </div>
          </div>

          <div className="filter-group">
            <span className="filter-label">
              Case duration, days (up to {facets.duration_max_days})
            </span>
            <div className="row">
              <input
                type="number"
                placeholder="min"
                min={0}
                value={value.minDays}
                onChange={(e) => set({ minDays: e.target.value })}
              />
              <span className="hint">to</span>
              <input
                type="number"
                placeholder="max"
                min={0}
                value={value.maxDays}
                onChange={(e) => set({ maxDays: e.target.value })}
              />
            </div>
          </div>

          <div className="filter-group">
            <span className="filter-label">Activities</span>
            <div className="row">
              <select
                value={value.includeActivity}
                onChange={(e) => set({ includeActivity: e.target.value })}
              >
                <option value="">case includes…</option>
                {facets.activities.map((a) => (
                  <option key={a} value={a}>
                    includes: {a}
                  </option>
                ))}
              </select>
              <select
                value={value.excludeActivity}
                onChange={(e) => set({ excludeActivity: e.target.value })}
              >
                <option value="">case excludes…</option>
                {facets.activities.map((a) => (
                  <option key={a} value={a}>
                    excludes: {a}
                  </option>
                ))}
              </select>
              <label className="row" style={{ gap: 6, fontSize: 13.5 }}>
                <input
                  type="checkbox"
                  checked={value.onlyRework}
                  onChange={(e) => set({ onlyRework: e.target.checked })}
                />
                only cases with rework
              </label>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
