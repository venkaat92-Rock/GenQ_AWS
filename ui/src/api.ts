const KEY_STORAGE = 'processiq_api_key'
const WS_STORAGE = 'processiq_workspace'

export function getApiKey(): string | null {
  return localStorage.getItem(KEY_STORAGE)
}

export function getWorkspaceName(): string | null {
  return localStorage.getItem(WS_STORAGE)
}

export function saveWorkspace(apiKey: string, name: string) {
  localStorage.setItem(KEY_STORAGE, apiKey)
  localStorage.setItem(WS_STORAGE, name)
}

export function clearWorkspace() {
  localStorage.removeItem(KEY_STORAGE)
  localStorage.removeItem(WS_STORAGE)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...((options.headers as Record<string, string>) || {}),
  }
  const key = getApiKey()
  if (key) headers['X-API-Key'] = key
  if (options.body && typeof options.body === 'string') {
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(path, { ...options, headers })
  const text = await response.text()
  let data: any = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null // non-JSON body (e.g. a plain-text error page)
  }
  if (!response.ok) {
    const detail =
      data && data.detail
        ? String(data.detail)
        : `${response.status} ${response.statusText}${text ? ` — ${text.slice(0, 200)}` : ''}`
    throw new ApiError(response.status, detail)
  }
  return data as T
}

export async function uploadFile(file: File, table?: string) {
  const form = new FormData()
  form.append('file', file)
  if (table) form.append('table', table)
  return api('/api/sources/upload', { method: 'POST', body: form })
}

export async function uploadDocument(file: File, uploadedBy?: string) {
  const form = new FormData()
  form.append('file', file)
  if (uploadedBy) form.append('uploaded_by', uploadedBy)
  return api('/api/documents', { method: 'POST', body: form })
}
