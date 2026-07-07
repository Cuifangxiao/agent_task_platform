import type {
  AuthResponse,
  ParsedSseEvent,
  RequestLogEntry,
  SessionState,
} from './types'

interface ApiClientOptions {
  getApiBase: () => string
  getSession: () => SessionState | null
  setSession: (session: SessionState | null) => void
  addLog: (entry: Omit<RequestLogEntry, 'id' | 'time'>) => void
}

interface RequestOptions {
  method?: string
  auth?: boolean
  body?: unknown
  headers?: HeadersInit
  signal?: AbortSignal
  retry?: boolean
}

interface StreamOptions {
  auth?: boolean
  body?: unknown
  signal?: AbortSignal
  retry?: boolean
  onEvent: (event: ParsedSseEvent) => void
}

const JSON_HEADERS = {
  'Content-Type': 'application/json',
  Accept: 'application/json',
}

export function createApiClient(options: ApiClientOptions) {
  let refreshPromise: Promise<boolean> | null = null

  function addLog(entry: Omit<RequestLogEntry, 'id' | 'time'>) {
    options.addLog(entry)
  }

  function buildUrl(path: string) {
    const base = options.getApiBase().trim()
    const normalizedPath = path.startsWith('/') ? path : `/${path}`

    if (!base || base === '/') {
      return normalizedPath
    }

    if (/^https?:\/\//i.test(base)) {
      return `${base.replace(/\/+$/, '')}${normalizedPath}`
    }

    const relativeBase = base.startsWith('/') ? base : `/${base}`
    return `${relativeBase.replace(/\/+$/, '')}${normalizedPath}`.replace(/\/{2,}/g, '/')
  }

  async function readPayload(response: Response) {
    if (response.status === 204) {
      return null
    }

    const contentType = response.headers.get('content-type') ?? ''
    if (contentType.includes('application/json')) {
      try {
        return await response.json()
      } catch {
        return null
      }
    }

    try {
      const text = await response.text()
      return text || null
    } catch {
      return null
    }
  }

  function getErrorMessage(payload: unknown, fallback: string) {
    if (typeof payload === 'string' && payload.trim()) {
      return payload.trim()
    }

    if (isRecord(payload)) {
      const message = payload.message
      const error = payload.error
      if (typeof error === 'string' && error.trim()) {
        return typeof message === 'string' && message.trim()
          ? `${message.trim()}: ${error.trim()}`
          : error.trim()
      }
      if (typeof message === 'string' && message.trim()) {
        return message.trim()
      }
    }

    return fallback
  }

  async function refreshAccessToken() {
    if (refreshPromise) {
      return refreshPromise
    }

    const currentSession = options.getSession()
    if (!currentSession?.refresh_token) {
      return false
    }

    refreshPromise = (async () => {
      const path = '/refresh'
      const payload = { refresh_token: currentSession.refresh_token }
      const response = await fetch(buildUrl(path), {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify(payload),
      })

      const data = await readPayload(response)
      addLog({
        method: 'POST',
        path,
        status: response.status,
        request: payload,
        response: data,
        note: '自动刷新 access token',
      })

      if (!response.ok || !isAuthResponse(data)) {
        options.setSession(null)
        return false
      }

      options.setSession(toSessionState(data))
      return true
    })()

    try {
      return await refreshPromise
    } finally {
      refreshPromise = null
    }
  }

  async function request<T>(path: string, requestOptions: RequestOptions = {}) {
    const method = requestOptions.method ?? 'GET'
    const auth = requestOptions.auth ?? false
    const retry = requestOptions.retry ?? true

    if (auth && !options.getSession()?.access_token) {
      throw new Error('请先登录再调用受保护接口。')
    }

    const headers = new Headers(requestOptions.headers ?? {})
    const bodyIsFormData = requestOptions.body instanceof FormData
    if (requestOptions.body !== undefined && !bodyIsFormData && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    if (!headers.has('Accept')) {
      headers.set('Accept', 'application/json')
    }

    const token = options.getSession()?.access_token
    if (auth && token) {
      headers.set('Authorization', `Bearer ${token}`)
    }

    const requestBody: BodyInit | undefined =
      requestOptions.body === undefined
        ? undefined
        : bodyIsFormData
          ? (requestOptions.body as FormData)
          : JSON.stringify(requestOptions.body)

    const response = await fetch(buildUrl(path), {
      method,
      headers,
      body: requestBody,
      signal: requestOptions.signal,
    })

    if (auth && response.status === 401 && retry) {
      const refreshed = await refreshAccessToken()
      if (refreshed) {
        return request<T>(path, { ...requestOptions, retry: false })
      }
    }

    const payload = await readPayload(response)
    addLog({
      method,
      path,
      status: response.status,
      request: toLoggableBody(requestOptions.body),
      response: payload,
    })

    if (!response.ok) {
      throw new Error(getErrorMessage(payload, `${method} ${path} failed`))
    }

    return payload as T
  }

  async function stream(path: string, streamOptions: StreamOptions) {
    const method = 'POST'
    const auth = streamOptions.auth ?? false
    const retry = streamOptions.retry ?? true

    if (auth && !options.getSession()?.access_token) {
      throw new Error('请先登录再调用流式接口。')
    }

    const bodyIsFormData = streamOptions.body instanceof FormData
    const headers = new Headers({
      Accept: 'text/event-stream',
    })
    if (!bodyIsFormData) {
      headers.set('Content-Type', 'application/json')
    }

    const token = options.getSession()?.access_token
    if (auth && token) {
      headers.set('Authorization', `Bearer ${token}`)
    }

    const requestBody: BodyInit = bodyIsFormData
      ? (streamOptions.body as FormData)
      : JSON.stringify(streamOptions.body ?? {})

    const response = await fetch(buildUrl(path), {
      method,
      headers,
      body: requestBody,
      signal: streamOptions.signal,
    })

    if (auth && response.status === 401 && retry) {
      const refreshed = await refreshAccessToken()
      if (refreshed) {
        return stream(path, { ...streamOptions, retry: false })
      }
    }

    const contentType = response.headers.get('content-type') ?? ''
    if (!response.ok || !contentType.includes('text/event-stream')) {
      const payload = await readPayload(response)
      addLog({
        method,
        path,
        status: response.status,
        request: toLoggableBody(streamOptions.body),
        response: payload,
      })
      throw new Error(getErrorMessage(payload, `${method} ${path} failed`))
    }

    if (!response.body) {
      addLog({
        method,
        path,
        status: response.status,
        request: toLoggableBody(streamOptions.body),
        response: { message: 'response body is empty' },
      })
      throw new Error('服务端没有返回可读取的流。')
    }

    let preview = ''
    let finalEvent = 'done'

    try {
      await consumeEventStream(response.body, (event) => {
        if (isRecord(event.data)) {
          const delta = event.data.delta
          const error = event.data.error
          if (typeof delta === 'string') {
            preview += delta
          }
          if (typeof error === 'string' && error.trim()) {
            finalEvent = 'error'
          }
        }
        streamOptions.onEvent(event)
      })

      addLog({
        method,
        path,
        status: 'stream',
        request: toLoggableBody(streamOptions.body),
        response: {
          state: finalEvent,
          preview,
        },
      })
    } catch (error) {
      const note = error instanceof DOMException && error.name === 'AbortError' ? 'stream aborted' : undefined
      addLog({
        method,
        path,
        status: note ? 'aborted' : 'stream-error',
        request: toLoggableBody(streamOptions.body),
        response: {
          state: note ?? 'stream error',
          preview,
          error: error instanceof Error ? error.message : String(error),
        },
        note,
      })
      throw error
    }
  }

  return {
    request,
    refreshAccessToken,
    stream,
  }
}

function toLoggableBody(body: unknown) {
  if (!(body instanceof FormData)) {
    return body
  }

  const value: Record<string, unknown> = {}
  for (const [key, entry] of body.entries()) {
    if (entry instanceof File) {
      value[key] = {
        name: entry.name,
        size: entry.size,
        type: entry.type,
      }
      continue
    }
    value[key] = entry
  }
  return value
}

async function consumeEventStream(stream: ReadableStream<Uint8Array>, onEvent: (event: ParsedSseEvent) => void) {
  const reader = stream.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done }).replace(/\r/g, '')

    let separatorIndex = buffer.indexOf('\n\n')
    while (separatorIndex >= 0) {
      const block = buffer.slice(0, separatorIndex).trim()
      buffer = buffer.slice(separatorIndex + 2)
      if (block) {
        const event = parseEventBlock(block)
        if (event) {
          onEvent(event)
        }
      }
      separatorIndex = buffer.indexOf('\n\n')
    }

    if (done) {
      const tail = buffer.trim()
      if (tail) {
        const event = parseEventBlock(tail)
        if (event) {
          onEvent(event)
        }
      }
      return
    }
  }
}

function parseEventBlock(block: string): ParsedSseEvent | null {
  const lines = block.split('\n')
  let eventName = 'message'
  const dataLines: string[] = []

  for (const line of lines) {
    if (line.startsWith(':')) {
      continue
    }
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim() || 'message'
      continue
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  if (dataLines.length === 0) {
    return null
  }

  const rawData = dataLines.join('\n')
  return {
    event: eventName,
    data: parseEventData(rawData),
  }
}

function parseEventData(rawData: string) {
  try {
    return JSON.parse(rawData)
  } catch {
    return rawData
  }
}

function isRecord(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null
}

function isAuthResponse(value: unknown): value is AuthResponse {
  return (
    isRecord(value) &&
    typeof value.access_token === 'string' &&
    typeof value.refresh_token === 'string' &&
    typeof value.token_type === 'string'
  )
}

function toSessionState(value: AuthResponse): SessionState {
  return {
    access_token: value.access_token,
    refresh_token: value.refresh_token,
    token_type: value.token_type,
    session_id: value.session_id,
    device: value.device,
    access_expires_at: value.access_expires_at,
    refresh_expires_at: value.refresh_expires_at,
  }
}
