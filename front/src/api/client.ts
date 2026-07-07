import type {
  AgentConfig,
  AgentConfigPayload,
  AgentDefinition,
  CredentialTestResponse,
  LoginResponse,
  MeResponse,
  ModelConfig,
  ModelConfigPayload,
  ParsedSseEvent,
  PptConversationDetail,
  PptConversationSummary,
  PptGeneratePayload,
  PptResumePayload,
  Provider,
  RequestLogEntry,
  SessionState,
} from '@/types'

const API_BASE = '/api'

export interface ApiClientOptions {
  getSession: () => SessionState | null
  setSession: (session: SessionState | null) => void
  onUnauthorized: () => void
  onLog: (entry: Omit<RequestLogEntry, 'id' | 'time'>) => void
}

export interface DownloadResult {
  blob: Blob
  filename: string
}

interface RequestOptions {
  method?: string
  auth?: boolean
  body?: unknown
  signal?: AbortSignal
  retry?: boolean
}

export function createApiClient(options: ApiClientOptions) {
  let refreshPromise: Promise<boolean> | null = null

  async function request<T>(path: string, requestOptions: RequestOptions = {}): Promise<T> {
    const method = requestOptions.method ?? 'GET'
    const headers = new Headers({ Accept: 'application/json' })

    if (requestOptions.body !== undefined) {
      headers.set('Content-Type', 'application/json')
    }

    if (requestOptions.auth) {
      const token = options.getSession()?.access_token
      if (!token) {
        options.onUnauthorized()
        throw new Error('登录状态已失效，请重新登录。')
      }
      headers.set('Authorization', `Bearer ${token}`)
    }

    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: requestOptions.body === undefined ? undefined : JSON.stringify(requestOptions.body),
      signal: requestOptions.signal,
    })

    if (requestOptions.auth && response.status === 401 && requestOptions.retry !== false) {
      const refreshed = await refreshAccessToken()
      if (refreshed) {
        return request<T>(path, { ...requestOptions, retry: false })
      }
    }

    const payload = await readPayload(response)
    options.onLog({ method, path, status: response.status })

    if (!response.ok) {
      if (response.status === 401) {
        options.onUnauthorized()
      }
      throw new Error(getErrorMessage(payload, `${method} ${path} 请求失败`))
    }

    return payload as T
  }

  async function download(path: string, requestOptions: RequestOptions = {}): Promise<DownloadResult> {
    const method = requestOptions.method ?? 'GET'
    const headers = new Headers()

    if (requestOptions.auth) {
      const token = options.getSession()?.access_token
      if (!token) {
        options.onUnauthorized()
        throw new Error('Login session expired, please sign in again.')
      }
      headers.set('Authorization', `Bearer ${token}`)
    }

    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      signal: requestOptions.signal,
    })

    if (requestOptions.auth && response.status === 401 && requestOptions.retry !== false) {
      const refreshed = await refreshAccessToken()
      if (refreshed) {
        return download(path, { ...requestOptions, retry: false })
      }
    }

    options.onLog({ method, path, status: response.status })

    if (!response.ok) {
      const payload = await readPayload(response)
      if (response.status === 401) {
        options.onUnauthorized()
      }
      throw new Error(getErrorMessage(payload, `${method} ${path} request failed`))
    }

    return {
      blob: await response.blob(),
      filename: parseDownloadFilename(response.headers.get('Content-Disposition')) ?? 'download.bin',
    }
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
        const auth = await request<LoginResponse>('/refresh', {
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

  async function streamPpt(
    body: PptGeneratePayload,
    onEvent: (event: ParsedSseEvent) => void,
    signal: AbortSignal,
  ) {
    const token = options.getSession()?.access_token
    if (!token) {
      options.onUnauthorized()
      throw new Error('请先登录再生成 PPT。')
    }

    const formData = toPptGenerateFormData(body)
    const response = await fetch(`${API_BASE}/ppt-agent/generate`, {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream',
        Authorization: `Bearer ${token}`,
      },
      body: formData,
      signal,
    })

    if (response.status === 401) {
      const refreshed = await refreshAccessToken()
      if (refreshed) {
        return streamPpt(body, onEvent, signal)
      }
    }

    if (!response.ok || !response.body) {
      const payload = await readPayload(response)
      throw new Error(getErrorMessage(payload, '生成请求启动失败。'))
    }

    options.onLog({ method: 'POST', path: '/ppt-agent/generate', status: 'stream' })
    await consumeEventStream(response.body, onEvent)
  }

  async function resumePpt(
    body: PptResumePayload,
    onEvent: (event: ParsedSseEvent) => void,
    signal: AbortSignal,
  ) {
    const token = options.getSession()?.access_token
    if (!token) {
      options.onUnauthorized()
      throw new Error('请先登录再继续生成 PPT。')
    }

    const response = await fetch(`${API_BASE}/ppt-agent/resume`, {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
      signal,
    })

    if (response.status === 401) {
      const refreshed = await refreshAccessToken()
      if (refreshed) {
        return resumePpt(body, onEvent, signal)
      }
    }

    if (!response.ok || !response.body) {
      const payload = await readPayload(response)
      throw new Error(getErrorMessage(payload, '继续生成请求启动失败。'))
    }

    options.onLog({ method: 'POST', path: '/ppt-agent/resume', status: 'stream' })
    await consumeEventStream(response.body, onEvent)
  }

  return {
    login: (username: string, password: string) =>
      request<LoginResponse>('/login', {
        method: 'POST',
        body: { username, password, device: 'front-web' },
      }),
    register: (username: string, password: string) =>
      request<{ message: string }>('/register', { method: 'POST', body: { username, password } }),
    logout: () => request<{ message: string }>('/logout', { method: 'POST', auth: true }),
    me: () => request<MeResponse>('/me', { auth: true }),
    health: () => request<{ status: string }>('/healthz'),
    providers: () => request<{ providers: Provider[] }>('/providers', { auth: true }),
    modelConfigs: () => request<ModelConfig[]>('/model-configs', { auth: true }),
    createModelConfig: (payload: ModelConfigPayload) =>
      request<ModelConfig>('/model-configs', { method: 'POST', auth: true, body: payload }),
    updateModelConfig: (id: number, payload: Partial<ModelConfigPayload>) =>
      request<ModelConfig>(`/model-configs/${id}`, { method: 'PUT', auth: true, body: payload }),
    deleteModelConfig: (id: number) =>
      request<{ message: string }>(`/model-configs/${id}`, { method: 'DELETE', auth: true }),
    testCredential: (payload: { provider: string; api_key: string; base_url?: string }) =>
      request<CredentialTestResponse>('/provider-credentials/test', { method: 'POST', auth: true, body: payload }),
    agents: () => request<AgentDefinition[]>('/agents', { auth: true }),
    agentConfigs: () => request<AgentConfig[]>('/agent-configs', { auth: true }),
    createAgentConfig: (payload: AgentConfigPayload & { agent_id: number }) =>
      request<AgentConfig>('/agent-configs', { method: 'POST', auth: true, body: payload }),
    updateAgentConfig: (id: number, payload: AgentConfigPayload) =>
      request<AgentConfig>(`/agent-configs/${id}`, { method: 'PUT', auth: true, body: payload }),
    deleteAgentConfig: (id: number) =>
      request<{ message: string }>(`/agent-configs/${id}`, { method: 'DELETE', auth: true }),
    pptConversations: () => request<PptConversationSummary[]>('/ppt-agent/conversations', { auth: true }),
    pptConversation: (id: number) => request<PptConversationDetail>(`/ppt-agent/conversations/${id}`, { auth: true }),
    downloadPptArtifact: (requestId: string) =>
      download(`/ppt-agent/artifacts/${encodeURIComponent(requestId)}`, { auth: true }),
    streamPpt,
    resumePpt,
  }
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

async function readPayload(response: Response) {
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

function getErrorMessage(payload: unknown, fallback: string) {
  if (typeof payload === 'string') {
    return payload
  }
  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>
    const message = typeof record.message === 'string' ? record.message : ''
    const error = typeof record.error === 'string' ? record.error : ''
    return [message, error].filter(Boolean).join(': ') || fallback
  }
  return fallback
}

function toPptGenerateFormData(body: PptGeneratePayload) {
  const formData = new FormData()
  if (body.agent_config_id !== undefined) {
    formData.append('agent_config_id', String(body.agent_config_id))
  }
  formData.append('description', body.description)
  if (body.source_file) {
    formData.append('source_file', body.source_file)
  }
  return formData
}

async function consumeEventStream(stream: ReadableStream<Uint8Array>, onEvent: (event: ParsedSseEvent) => void) {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done }).replace(/\r/g, '')

    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary).trim()
      buffer = buffer.slice(boundary + 2)
      if (block) {
        const parsed = parseEventBlock(block)
        if (parsed) {
          onEvent(parsed)
        }
      }
      boundary = buffer.indexOf('\n\n')
    }

    if (done) {
      const tail = buffer.trim()
      if (tail) {
        const parsed = parseEventBlock(tail)
        if (parsed) {
          onEvent(parsed)
        }
      }
      return
    }
  }
}

function parseEventBlock(block: string): ParsedSseEvent | null {
  const lines = block.split('\n')
  let event = 'message'
  const data: string[] = []

  for (const line of lines) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim() || event
    }
    if (line.startsWith('data:')) {
      data.push(line.slice(5).trimStart())
    }
  }

  if (data.length === 0) {
    return null
  }

  const raw = data.join('\n')
  try {
    return { event, data: JSON.parse(raw) }
  } catch {
    return { event, data: raw }
  }
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

  const plainMatch = contentDisposition.match(/filename="?([^"]+)"?/i)
  if (plainMatch?.[1]) {
    return plainMatch[1]
  }

  return null
}
