import type { LoginResponse, RequestLogEntry, SessionState } from '@/types'

export const API_PREFIX = '/api/v1'

export interface ApiErrorDetail {
  field?: string
  reason: string
}

export interface ApiMeta {
  request_id: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId?: string
  readonly details: ApiErrorDetail[]

  constructor(options: {
    message: string
    status: number
    code?: string
    requestId?: string
    details?: ApiErrorDetail[]
  }) {
    super(options.message)
    this.name = 'ApiError'
    this.status = options.status
    this.code = options.code ?? 'request_failed'
    this.requestId = options.requestId
    this.details = options.details ?? []
  }
}

export interface HttpClientOptions {
  getSession: () => SessionState | null
  setSession: (session: SessionState | null) => void
  onUnauthorized: () => void
  onLog: (entry: Omit<RequestLogEntry, 'id' | 'time'>) => void
}

export interface RequestOptions {
  method?: string
  auth?: boolean
  body?: unknown
  headers?: HeadersInit
  signal?: AbortSignal
  retry?: boolean
}

export interface DownloadResult {
  blob: Blob
  filename: string
}

interface SuccessEnvelope<T> {
  data: T
  meta: ApiMeta
}

interface ErrorEnvelope {
  error: {
    code?: string
    message?: string
    details?: ApiErrorDetail[]
    request_id?: string
  }
}

export type HttpClient = ReturnType<typeof createHttpClient>

export function createHttpClient(options: HttpClientOptions) {
  let refreshPromise: Promise<boolean> | null = null

  async function request<T>(path: string, requestOptions: RequestOptions = {}): Promise<T> {
    const response = await send(path, requestOptions, 'application/json')
    const payload = await readPayload(response)

    if (!response.ok) {
      throw toApiError(payload, response, requestOptions.method ?? 'GET', path)
    }

    return unwrapData<T>(payload)
  }

  async function openStream(path: string, requestOptions: RequestOptions = {}) {
    const response = await send(path, requestOptions, 'text/event-stream')
    if (!response.ok || !response.body) {
      const payload = await readPayload(response)
      throw toApiError(payload, response, requestOptions.method ?? 'GET', path)
    }
    return response.body
  }

  async function download(path: string, requestOptions: RequestOptions = {}): Promise<DownloadResult> {
    const response = await send(path, requestOptions, '*/*')
    if (!response.ok) {
      const payload = await readPayload(response)
      throw toApiError(payload, response, requestOptions.method ?? 'GET', path)
    }
    return {
      blob: await response.blob(),
      filename: parseDownloadFilename(response.headers.get('Content-Disposition')) ?? 'download.bin',
    }
  }

  async function downloadBlob(path: string, requestOptions: RequestOptions = {}) {
    return (await download(path, requestOptions)).blob
  }

  async function send(path: string, requestOptions: RequestOptions, accept: string): Promise<Response> {
    const method = requestOptions.method ?? 'GET'
    const headers = new Headers(requestOptions.headers)
    headers.set('Accept', accept)

    const body = serializeBody(requestOptions.body, headers)
    if (requestOptions.auth) {
      const token = options.getSession()?.access_token
      if (!token) {
        options.onUnauthorized()
        throw new ApiError({ message: '登录状态已失效，请重新登录。', status: 401, code: 'unauthorized' })
      }
      headers.set('Authorization', `Bearer ${token}`)
    }

    const response = await fetch(toApiUrl(path), {
      method,
      headers,
      body,
      signal: requestOptions.signal,
    })
    options.onLog({ method, path: toApiUrl(path), status: response.status })

    if (requestOptions.auth && response.status === 401 && requestOptions.retry !== false) {
      if (await refreshAccessToken()) {
        return send(path, { ...requestOptions, retry: false }, accept)
      }
      options.onUnauthorized()
    }

    return response
  }

  async function refreshAccessToken() {
    if (refreshPromise) {
      return refreshPromise
    }
    const current = options.getSession()
    if (!current?.refresh_token) {
      options.onUnauthorized()
      return false
    }

    refreshPromise = (async () => {
      try {
        const auth = await request<LoginResponse>('/auth/refresh', {
          method: 'POST',
          body: { refresh_token: current.refresh_token },
          retry: false,
        })
        options.setSession(toSession(auth))
        return true
      } catch {
        options.onUnauthorized()
        return false
      }
    })()

    try {
      return await refreshPromise
    } finally {
      refreshPromise = null
    }
  }

  return { request, openStream, download, downloadBlob }
}

export function toSession(auth: LoginResponse): SessionState {
  return {
    access_token: auth.access_token,
    refresh_token: auth.refresh_token,
    token_type: auth.token_type,
    session_id: auth.session_id,
    device: auth.device,
    access_expires_at: auth.access_expires_at,
    refresh_expires_at: auth.refresh_expires_at,
  }
}

export function toApiUrl(path: string) {
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${API_PREFIX}${normalized}`
}

function serializeBody(body: unknown, headers: Headers) {
  if (body === undefined || body === null) {
    return undefined
  }
  if (body instanceof FormData || body instanceof Blob || body instanceof URLSearchParams || typeof body === 'string') {
    return body
  }
  headers.set('Content-Type', 'application/json')
  return JSON.stringify(body)
}

async function readPayload(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return null
  }
  const text = await response.text()
  if (!text) {
    return null
  }
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function unwrapData<T>(payload: unknown): T {
  if (isSuccessEnvelope<T>(payload)) {
    return payload.data
  }
  return payload as T
}

function isSuccessEnvelope<T>(payload: unknown): payload is SuccessEnvelope<T> {
  return Boolean(
    payload &&
      typeof payload === 'object' &&
      Object.prototype.hasOwnProperty.call(payload, 'data') &&
      Object.prototype.hasOwnProperty.call(payload, 'meta'),
  )
}

function toApiError(payload: unknown, response: Response, method: string, path: string) {
  if (isErrorEnvelope(payload)) {
    return new ApiError({
      message: payload.error.message || `${method} ${path} 请求失败`,
      status: response.status,
      code: payload.error.code,
      requestId: payload.error.request_id,
      details: payload.error.details,
    })
  }
  if (typeof payload === 'string' && payload) {
    return new ApiError({ message: payload, status: response.status })
  }
  return new ApiError({ message: `${method} ${path} 请求失败`, status: response.status })
}

function isErrorEnvelope(payload: unknown): payload is ErrorEnvelope {
  if (!payload || typeof payload !== 'object') {
    return false
  }
  const error = (payload as Record<string, unknown>).error
  return Boolean(error && typeof error === 'object')
}

function parseDownloadFilename(contentDisposition: string | null) {
  if (!contentDisposition) {
    return null
  }
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1])
    } catch {
      return utf8Match[1]
    }
  }
  return contentDisposition.match(/filename="?([^";]+)"?/i)?.[1] ?? null
}
