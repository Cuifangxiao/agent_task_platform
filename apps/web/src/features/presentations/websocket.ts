import { normalizeTaskEvent, taskSnapshotWatermark } from './lib/taskProjection.ts'
import type { PptAgentEvent, PptTask, PptTaskStatus } from './types.ts'

export const PRESENTATION_WS_PATH = '/api/v1/ppt/ws'
export const PRESENTATION_WS_SCHEMA_VERSION = '1'
export const PRESENTATION_WS_PROTOCOL = 'agent-task-v1'

export type PresentationWebSocketConnectionState =
  | 'connecting'
  | 'open'
  | 'reconnecting'
  | 'closed'

export interface PresentationSocketLike {
  readonly readyState: number
  onopen: ((event: Event) => void) | null
  onmessage: ((event: MessageEvent<unknown>) => void) | null
  onerror: ((event: Event) => void) | null
  onclose: ((event: CloseEvent) => void) | null
  send(data: string): void
  close(code?: number, reason?: string): void
}

export interface PresentationWebSocketFrame {
  schema_version: string
  type: 'connection.ready' | 'command.ack' | 'command.error' | 'run.snapshot' | 'run.event' | 'pong'
  request_id?: string
  created_at?: string
  payload: Record<string, unknown>
}

export interface PresentationWebSocketHandlers {
  onSnapshot: (task: PptTask) => void
  onEvent: (event: PptAgentEvent) => void
  onConnectionState: (state: PresentationWebSocketConnectionState, error?: Error) => void
  onSequenceGap?: (runId: string, expected: number, received: number) => void
  onSubscriptionError?: (runId: string, error: Error) => void
}

export interface PresentationWebSocketOptions extends PresentationWebSocketHandlers {
  getAccessToken: () => string
  url?: string
  createSocket?: (url: string, protocols: string[]) => PresentationSocketLike
  createRequestId?: () => string
  setTimeout?: (callback: () => void, delay: number) => unknown
  clearTimeout?: (handle: unknown) => void
  random?: () => number
  reconnectBaseDelayMs?: number
  reconnectMaxDelayMs?: number
  commandTimeoutMs?: number
  heartbeatIntervalMs?: number
  connectionReadyTimeoutMs?: number
}

export interface PresentationWebSocketClient {
  connect(): void
  subscribe(runId: string, after?: number): Promise<void>
  unsubscribe(runId: string): Promise<void>
  cancel(runId: string): Promise<void>
  ping(): Promise<void>
  close(): void
}

interface SubscriptionState {
  cursor: number
  repairingGap: boolean
  desired: boolean
  activeGeneration: number | null
  inFlightGeneration: number | null
  activationSettled: boolean
  activationPromise: Promise<void>
  resolveActivation: () => void
  rejectActivation: (error: Error) => void
}

interface PendingCommand {
  resolve: () => void
  reject: (error: Error) => void
  timeout: unknown
}

const SERVER_FRAME_TYPES = new Set<PresentationWebSocketFrame['type']>([
  'connection.ready',
  'command.ack',
  'command.error',
  'run.snapshot',
  'run.event',
  'pong',
])

const TASK_STATUSES = new Set<PptTaskStatus>(['running', 'succeeded', 'failed', 'cancelled'])

export function createPresentationWebSocket(options: PresentationWebSocketOptions): PresentationWebSocketClient {
  const createSocket = options.createSocket ?? defaultCreateSocket
  const createRequestId = options.createRequestId ?? defaultRequestId
  const scheduleTimeout = options.setTimeout ?? ((callback, delay) => globalThis.setTimeout(callback, delay))
  const cancelTimeout = options.clearTimeout ?? ((handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>))
  const random = options.random ?? Math.random
  const reconnectBaseDelayMs = options.reconnectBaseDelayMs ?? 500
  const reconnectMaxDelayMs = options.reconnectMaxDelayMs ?? 15_000
  const commandTimeoutMs = options.commandTimeoutMs ?? 8_000
  const heartbeatIntervalMs = options.heartbeatIntervalMs ?? 25_000
  const connectionReadyTimeoutMs = options.connectionReadyTimeoutMs ?? 8_000

  const subscriptions = new Map<string, SubscriptionState>()
  const pending = new Map<string, PendingCommand>()
  let socket: PresentationSocketLike | null = null
  let reconnectTimer: unknown = null
  let heartbeatTimer: unknown = null
  let readyTimer: unknown = null
  let reconnectAttempt = 0
  let socketGeneration = 0
  let manuallyClosed = false
  let applicationReady = false
  let connectionState: PresentationWebSocketConnectionState = 'closed'

  function connect() {
    if (manuallyClosed) manuallyClosed = false
    if (socket && (socket.readyState === 0 || socket.readyState === 1)) return
    openSocket(reconnectAttempt > 0 ? 'reconnecting' : 'connecting')
  }

  function openSocket(nextState: PresentationWebSocketConnectionState) {
    clearReconnectTimer()
    clearReadyTimer()
    applicationReady = false
    updateConnectionState(nextState)
    const accessToken = options.getAccessToken().trim()
    if (!accessToken) {
      scheduleReconnect(new Error('登录凭证不可用，无法建立 PPT 实时连接。'))
      return
    }

    try {
      const nextSocket = createSocket(
        options.url ?? presentationWebSocketUrl(),
        [PRESENTATION_WS_PROTOCOL, `bearer.${accessToken}`],
      )
      const generation = ++socketGeneration
      socket = nextSocket
      nextSocket.onopen = () => {
        if (socket !== nextSocket || manuallyClosed) return
        scheduleReadyTimeout(nextSocket, generation)
      }
      nextSocket.onmessage = (message) => {
        if (socket !== nextSocket) return
        const frame = parsePresentationWebSocketFrame(message.data)
        if (frame) handleFrame(frame)
      }
      nextSocket.onerror = () => {
        if (socket === nextSocket && !manuallyClosed) {
          options.onConnectionState(connectionState, new Error('PPT 实时连接发生网络错误。'))
        }
      }
      nextSocket.onclose = () => {
        if (socket !== nextSocket) return
        socket = null
        applicationReady = false
        clearReadyTimer()
        clearHeartbeatTimer()
        for (const subscription of subscriptions.values()) {
          subscription.activeGeneration = null
          subscription.inFlightGeneration = null
          subscription.repairingGap = false
        }
        rejectPending(new Error('PPT 实时连接已断开。'))
        if (manuallyClosed) {
          updateConnectionState('closed')
        } else {
          scheduleReconnect()
        }
      }
    } catch (cause) {
      socket = null
      scheduleReconnect(toError(cause))
    }
  }

  function handleFrame(frame: PresentationWebSocketFrame) {
    if (frame.type === 'connection.ready') {
      if (applicationReady) return
      applicationReady = true
      clearReadyTimer()
      reconnectAttempt = 0
      updateConnectionState('open')
      scheduleHeartbeat()
      for (const [runId, subscription] of subscriptions) {
        if (!subscription.desired) continue
        ensureSubscriptionSent(runId, subscription)
      }
      return
    }
    if (frame.type === 'command.ack' || frame.type === 'pong') {
      if (frame.request_id) settlePending(frame.request_id)
      return
    }
    if (frame.type === 'command.error') {
      const error = commandError(frame.payload)
      if (frame.request_id) settlePending(frame.request_id, error)
      return
    }
    if (frame.type === 'run.snapshot') {
      const snapshot = taskFromPayload(frame.payload)
      const subscription = snapshot ? subscriptionFor(snapshot.run_id || snapshot.task_id) : null
      if (snapshot && subscription) {
        options.onSnapshot(snapshot)
        const watermark = taskSnapshotWatermark(snapshot)
        if (snapshot.status !== 'running' && watermark !== null && subscription.cursor >= watermark) {
          subscription.desired = false
        }
      }
      return
    }

    const event = eventFromPayload(frame.payload)
    if (!event) return
    const runId = canonicalRunId(event)
    const subscription = runId ? subscriptionFor(runId) : null
    if (!runId || !subscription) return
    if (event.sequence <= subscription.cursor) return
    const expected = subscription.cursor + 1
    if (event.sequence !== expected) {
      options.onSequenceGap?.(runId, expected, event.sequence)
      requestGapRepair(runId, subscription)
      return
    }

    subscription.cursor = event.sequence
    subscription.repairingGap = false
    options.onEvent(event)
    if (event.type === 'TASK_COMPLETED' || event.type === 'TASK_FAILED') {
      subscriptions.delete(runId)
    }
  }

  function ensureSubscriptionSent(runId: string, subscription: SubscriptionState) {
    if (
      !subscription.desired
      || !isReady()
      || subscription.activeGeneration === socketGeneration
      || subscription.inFlightGeneration !== null
    ) return

    const generation = socketGeneration
    subscription.inFlightGeneration = generation
    void sendCommand('run.subscribe', { run_id: runId, after: subscription.cursor })
      .then(() => {
        if (
          subscriptions.get(runId) !== subscription
          || subscription.inFlightGeneration !== generation
          || socketGeneration !== generation
          || !isReady()
        ) return
        subscription.inFlightGeneration = null
        subscription.activeGeneration = generation
        if (!subscription.activationSettled) {
          subscription.activationSettled = true
          subscription.resolveActivation()
        }
      })
      .catch((error) => {
        if (subscriptions.get(runId) !== subscription || subscription.inFlightGeneration !== generation) return
        subscription.inFlightGeneration = null
        subscription.activeGeneration = null
        handleSubscriptionFailure(runId, subscription, toError(error))
      })
  }

  function handleSubscriptionFailure(runId: string, subscription: SubscriptionState, error: Error) {
    const detail = error as Error & { code?: string; retryable?: boolean }
    if (detail.code === 'event_cursor_ahead') {
      // The server watermark is diagnostic only. Replay from zero so a
      // restarted server can restore terminal and artifact history too.
      subscription.cursor = 0
      subscription.repairingGap = false
    }

    if (typeof detail.retryable === 'boolean' && !detail.retryable) {
      subscription.desired = false
      if (subscriptions.get(runId) === subscription) subscriptions.delete(runId)
      if (!subscription.activationSettled) {
        subscription.activationSettled = true
        subscription.rejectActivation(error)
      }
      options.onSubscriptionError?.(runId, error)
      return
    }

    options.onConnectionState(connectionState, error)
    if (isReady()) {
      socket?.close(
        typeof detail.retryable === 'boolean' ? 4001 : 4003,
        typeof detail.retryable === 'boolean' ? 'retryable subscribe error' : 'subscribe command failed',
      )
    }
  }

  function requestGapRepair(runId: string, subscription: SubscriptionState) {
    if (subscription.repairingGap || subscription.inFlightGeneration !== null || !isReady()) return
    subscription.repairingGap = true
    void sendCommand('run.subscribe', { run_id: runId, after: subscription.cursor }).catch((error) => {
      subscription.repairingGap = false
      handleSubscriptionFailure(runId, subscription, error)
    })
  }

  function subscriptionFor(runId: string) {
    const direct = subscriptions.get(runId)
    if (direct) return direct
    for (const [subscribedRunId, subscription] of subscriptions) {
      if (subscribedRunId === runId) return subscription
    }
    return null
  }

  function subscribe(runId: string, after = 0) {
    const normalizedRunId = runId.trim()
    if (!normalizedRunId) return Promise.reject(new Error('run_id 不能为空。'))
    const cursor = normalizeCursor(after)
    let subscription = subscriptions.get(normalizedRunId)
    if (!subscription) {
      subscription = createSubscriptionState(cursor)
      subscriptions.set(normalizedRunId, subscription)
    } else {
      subscription.cursor = Math.max(subscription.cursor, cursor)
      subscription.desired = true
    }
    if (!isReady()) {
      connect()
      return subscription.activationPromise
    }
    ensureSubscriptionSent(normalizedRunId, subscription)
    return subscription.activationPromise
  }

  function unsubscribe(runId: string) {
    const normalizedRunId = runId.trim()
    const subscription = subscriptions.get(normalizedRunId)
    subscriptions.delete(normalizedRunId)
    if (subscription && !subscription.activationSettled) {
      subscription.activationSettled = true
      subscription.rejectActivation(new Error('PPT run subscription was cancelled.'))
    }
    if (!normalizedRunId || !isReady()) return Promise.resolve()
    return sendCommand('run.unsubscribe', { run_id: normalizedRunId })
  }

  function cancel(runId: string) {
    const normalizedRunId = runId.trim()
    if (!normalizedRunId) return Promise.reject(new Error('run_id 不能为空。'))
    if (!isReady()) return Promise.reject(new Error('PPT 实时连接尚未就绪。'))
    return sendCommand('run.cancel', { run_id: normalizedRunId })
  }

  function ping() {
    if (!isReady()) return Promise.reject(new Error('PPT 实时连接尚未就绪。'))
    return sendCommand('ping', {})
  }

  function sendCommand(type: string, payload: Record<string, unknown>) {
    if (!socket || !isReady()) return Promise.reject(new Error('PPT 实时连接尚未就绪。'))
    const requestId = createRequestId()
    return new Promise<void>((resolve, reject) => {
      const timeout = scheduleTimeout(() => {
        pending.delete(requestId)
        reject(new Error(`${type} 命令等待响应超时。`))
      }, commandTimeoutMs)
      pending.set(requestId, { resolve, reject, timeout })
      try {
        socket?.send(JSON.stringify({
          schema_version: PRESENTATION_WS_SCHEMA_VERSION,
          type,
          request_id: requestId,
          payload,
        }))
      } catch (cause) {
        settlePending(requestId, toError(cause))
      }
    })
  }

  function close() {
    manuallyClosed = true
    clearReconnectTimer()
    clearReadyTimer()
    clearHeartbeatTimer()
    for (const subscription of subscriptions.values()) {
      if (!subscription.activationSettled) {
        subscription.activationSettled = true
        subscription.rejectActivation(new Error('PPT 实时连接已关闭。'))
      }
    }
    subscriptions.clear()
    rejectPending(new Error('PPT 实时连接已关闭。'))
    const current = socket
    socket = null
    applicationReady = false
    current?.close(1000, 'workspace closed')
    updateConnectionState('closed')
  }

  function scheduleReconnect(error?: Error) {
    if (manuallyClosed || reconnectTimer !== null) return
    reconnectAttempt += 1
    updateConnectionState('reconnecting', error)
    const exponential = Math.min(
      reconnectMaxDelayMs,
      reconnectBaseDelayMs * 2 ** Math.max(0, reconnectAttempt - 1),
    )
    const delay = Math.min(reconnectMaxDelayMs, exponential + exponential * 0.25 * random())
    reconnectTimer = scheduleTimeout(() => {
      reconnectTimer = null
      openSocket('reconnecting')
    }, delay)
  }

  function clearReconnectTimer() {
    if (reconnectTimer === null) return
    cancelTimeout(reconnectTimer)
    reconnectTimer = null
  }

  function scheduleReadyTimeout(nextSocket: PresentationSocketLike, generation: number) {
    clearReadyTimer()
    readyTimer = scheduleTimeout(() => {
      readyTimer = null
      if (socket !== nextSocket || socketGeneration !== generation || applicationReady || manuallyClosed) return
      const error = new Error('PPT 实时连接等待 connection.ready 超时。')
      options.onConnectionState(connectionState, error)
      nextSocket.close(4002, 'connection ready timeout')
    }, connectionReadyTimeoutMs)
  }

  function clearReadyTimer() {
    if (readyTimer === null) return
    cancelTimeout(readyTimer)
    readyTimer = null
  }

  function scheduleHeartbeat() {
    clearHeartbeatTimer()
    if (!isReady() || manuallyClosed) return
    heartbeatTimer = scheduleTimeout(() => {
      heartbeatTimer = null
      void ping()
        .then(scheduleHeartbeat)
        .catch((error) => {
          options.onConnectionState(connectionState, error)
          socket?.close(4000, 'heartbeat timeout')
        })
    }, heartbeatIntervalMs)
  }

  function clearHeartbeatTimer() {
    if (heartbeatTimer === null) return
    cancelTimeout(heartbeatTimer)
    heartbeatTimer = null
  }

  function settlePending(requestId: string, error?: Error) {
    const command = pending.get(requestId)
    if (!command) return
    pending.delete(requestId)
    cancelTimeout(command.timeout)
    if (error) command.reject(error)
    else command.resolve()
  }

  function rejectPending(error: Error) {
    for (const requestId of [...pending.keys()]) settlePending(requestId, error)
  }

  function isReady() {
    return applicationReady && socket?.readyState === 1
  }

  function updateConnectionState(state: PresentationWebSocketConnectionState, error?: Error) {
    connectionState = state
    options.onConnectionState(state, error)
  }

  return { connect, subscribe, unsubscribe, cancel, ping, close }
}

export function parsePresentationWebSocketFrame(data: unknown): PresentationWebSocketFrame | null {
  let value = data
  if (typeof value === 'string') {
    try {
      value = JSON.parse(value)
    } catch {
      return null
    }
  }
  if (!isRecord(value) || String(value.schema_version) !== PRESENTATION_WS_SCHEMA_VERSION) return null
  const type = String(value.type) as PresentationWebSocketFrame['type']
  if (!SERVER_FRAME_TYPES.has(type)) return null
  return {
    schema_version: PRESENTATION_WS_SCHEMA_VERSION,
    type,
    ...(typeof value.request_id === 'string' ? { request_id: value.request_id } : {}),
    ...(typeof value.created_at === 'string' ? { created_at: value.created_at } : {}),
    payload: isRecord(value.payload) ? value.payload : {},
  }
}

export function presentationWebSocketUrl(href = globalThis.location?.href) {
  if (!href) throw new Error('无法确定 PPT WebSocket 地址。')
  const url = new URL(PRESENTATION_WS_PATH, href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

function eventFromPayload(payload: Record<string, unknown>) {
  const raw = isRecord(payload.event) ? payload.event : payload
  const taskId = identifier(raw.task_id)
  const runId = identifier(raw.run_id)
  const wrapperRunId = identifier(payload.run_id)
  if ((taskId && runId && taskId !== runId) || (wrapperRunId && runId && wrapperRunId !== runId)) return null
  return normalizeTaskEvent({
    event: String(raw.type || 'message'),
    data: raw,
  })
}

function taskFromPayload(payload: Record<string, unknown>): PptTask | null {
  const raw = isRecord(payload.snapshot)
    ? payload.snapshot
    : isRecord(payload.task)
      ? payload.task
      : payload
  const taskId = identifier(raw.task_id)
  const runId = identifier(raw.run_id)
  const status = String(raw.status) as PptTaskStatus
  if (!TASK_STATUSES.has(status) || (!taskId && !runId) || (taskId && runId && taskId !== runId)) return null
  return {
    ...raw,
    task_id: taskId || runId,
    ...(runId ? { run_id: runId } : {}),
    status,
  } as PptTask
}

function canonicalRunId(event: PptAgentEvent) {
  if (event.task_id && event.run_id && event.task_id !== event.run_id) return ''
  return event.run_id || event.task_id
}

function commandError(payload: Record<string, unknown>) {
  const detail = isRecord(payload.error) ? payload.error : payload
  const error = Object.assign(new Error(String(detail.message || 'WebSocket 命令执行失败。')), {
    code: typeof detail.code === 'string' ? detail.code : 'command_error',
    command: typeof detail.command === 'string' ? detail.command : '',
    runId: typeof detail.run_id === 'string' ? detail.run_id : '',
    retryable: detail.retryable === true,
    lastEventSequence: optionalNonNegativeInteger(detail.last_event_sequence),
  })
  return error
}

function optionalNonNegativeInteger(value: unknown) {
  const number = Number(value)
  return Number.isSafeInteger(number) && number >= 0 ? number : undefined
}

function createSubscriptionState(cursor: number): SubscriptionState {
  let resolveActivation!: () => void
  let rejectActivation!: (error: Error) => void
  const activationPromise = new Promise<void>((resolve, reject) => {
    resolveActivation = resolve
    rejectActivation = reject
  })
  return {
    cursor,
    repairingGap: false,
    desired: true,
    activeGeneration: null,
    inFlightGeneration: null,
    activationSettled: false,
    activationPromise,
    resolveActivation,
    rejectActivation,
  }
}

function normalizeCursor(value: number) {
  return Number.isSafeInteger(value) && value > 0 ? value : 0
}

function identifier(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function toError(cause: unknown) {
  return cause instanceof Error ? cause : new Error(String(cause))
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function defaultCreateSocket(url: string, protocols: string[]) {
  return new WebSocket(url, protocols)
}

function defaultRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `ws-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}
