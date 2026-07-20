import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createPresentationWebSocket,
  parsePresentationWebSocketFrame,
  presentationWebSocketUrl,
  type PresentationSocketLike,
} from '../../apps/web/src/features/presentations/websocket.ts'
import type { PptAgentEvent, PptTask } from '../../apps/web/src/features/presentations/types.ts'

class FakeSocket implements PresentationSocketLike {
  readyState = 0
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  readonly sent: Array<Record<string, unknown>> = []
  closeCode: number | undefined
  closeReason = ''

  send(data: string) {
    this.sent.push(JSON.parse(data) as Record<string, unknown>)
  }

  open() {
    this.readyState = 1
    this.onopen?.({} as Event)
  }

  receive(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent<string>)
  }

  close(code?: number, reason?: string) {
    if (this.readyState === 3) return
    this.closeCode = code
    this.closeReason = reason ?? ''
    this.readyState = 3
    this.onclose?.({} as CloseEvent)
  }
}

class FakeScheduler {
  private nextId = 1
  private readonly tasks = new Map<number, { callback: () => void; delay: number }>()

  setTimeout = (callback: () => void, delay: number) => {
    const id = this.nextId++
    this.tasks.set(id, { callback, delay })
    return id
  }

  clearTimeout = (handle: unknown) => {
    this.tasks.delete(Number(handle))
  }

  nextDelay() {
    return [...this.tasks.values()].sort((left, right) => left.delay - right.delay)[0]?.delay
  }

  runNext() {
    const next = [...this.tasks.entries()].sort((left, right) => left[1].delay - right[1].delay)[0]
    assert.ok(next, 'expected a scheduled timer')
    this.tasks.delete(next[0])
    next[1].callback()
  }
}

function serverFrame(
  type: string,
  payload: Record<string, unknown> = {},
  requestId?: string,
) {
  return {
    schema_version: '1',
    type,
    ...(requestId ? { request_id: requestId } : {}),
    created_at: '2026-07-20T00:00:00Z',
    payload,
  }
}

function command(socket: FakeSocket, index = socket.sent.length - 1) {
  const value = socket.sent[index]
  assert.ok(value)
  return value
}

function ack(socket: FakeSocket, value = command(socket)) {
  socket.receive(serverFrame('command.ack', {}, String(value.request_id)))
}

function event(runId: string, sequence: number, type = 'STEP_PROGRESS') {
  return serverFrame('run.event', {
    schema_version: '1',
    event_id: `${runId}:${sequence}`,
    thread_id: `thread-${runId}`,
    run_id: runId,
    task_id: runId,
    sequence,
    type,
    stage: type.startsWith('TASK_') ? 'FINISHED' : 'GENERATING_PAGES',
    message: `${runId} event ${sequence}`,
  })
}

test('WS frames and URLs enforce protocol v1 without putting tokens in the query string', async () => {
  assert.equal(
    presentationWebSocketUrl('https://app.example.com/ppt'),
    'wss://app.example.com/api/v1/ppt/ws',
  )
  assert.equal(parsePresentationWebSocketFrame('{"schema_version":"2","type":"pong","payload":{}}'), null)
  assert.deepEqual(
    parsePresentationWebSocketFrame(JSON.stringify(serverFrame('connection.ready', { connection_id: 'c-1' }))),
    serverFrame('connection.ready', { connection_id: 'c-1' }),
  )

  const sockets: FakeSocket[] = []
  let capturedUrl = ''
  let capturedProtocols: string[] = []
  const states: string[] = []
  const client = createPresentationWebSocket({
    url: 'wss://app.example.com/api/v1/ppt/ws',
    getAccessToken: () => 'header.payload.signature',
    createSocket: (url, protocols) => {
      capturedUrl = url
      capturedProtocols = protocols
      const socket = new FakeSocket()
      sockets.push(socket)
      return socket
    },
    createRequestId: () => 'request-1',
    onSnapshot: () => {},
    onEvent: () => {},
    onConnectionState: (state) => states.push(state),
  })

  client.connect()
  const socket = sockets[0] as FakeSocket
  socket.open()
  const subscription = client.subscribe('run-1', 7)
  const concurrentSubscription = client.subscribe('run-1', 7)
  let activated = false
  void subscription.then(() => { activated = true })
  assert.equal(capturedUrl, 'wss://app.example.com/api/v1/ppt/ws')
  assert.deepEqual(capturedProtocols, ['agent-task-v1', 'bearer.header.payload.signature'])
  assert.equal(capturedUrl.includes('header.payload.signature'), false)
  assert.equal(socket.sent.length, 0)
  assert.equal(concurrentSubscription, subscription)
  await Promise.resolve()
  assert.equal(activated, false)
  assert.deepEqual(states, ['connecting'])
  socket.receive(serverFrame('connection.ready'))
  assert.deepEqual(command(socket), {
    schema_version: '1',
    type: 'run.subscribe',
    request_id: 'request-1',
    payload: { run_id: 'run-1', after: 7 },
  })
  await Promise.resolve()
  assert.equal(activated, false)
  ack(socket)
  await subscription
  await concurrentSubscription
  assert.equal(activated, true)
  assert.deepEqual(states, ['connecting', 'open'])
  client.close()
})

test('one socket multiplexes runs and only omits terminal snapshots whose replay watermark is caught up', async () => {
  const scheduler = new FakeScheduler()
  const sockets: FakeSocket[] = []
  const received: PptAgentEvent[] = []
  const snapshots: PptTask[] = []
  const gaps: Array<[string, number, number]> = []
  const subscriptionErrors: Array<[string, string]> = []
  let requestSequence = 0
  const client = createPresentationWebSocket({
    url: 'ws://test/api/v1/ppt/ws',
    getAccessToken: () => 'token',
    createSocket: () => {
      const socket = new FakeSocket()
      sockets.push(socket)
      return socket
    },
    createRequestId: () => `request-${++requestSequence}`,
    setTimeout: scheduler.setTimeout,
    clearTimeout: scheduler.clearTimeout,
    random: () => 0,
    reconnectBaseDelayMs: 10,
    heartbeatIntervalMs: 25_000,
    onSnapshot: (snapshot) => snapshots.push(snapshot),
    onEvent: (item) => received.push(item),
    onConnectionState: () => {},
    onSequenceGap: (runId, expected, actual) => gaps.push([runId, expected, actual]),
    onSubscriptionError: (runId, error) => subscriptionErrors.push([runId, error.message]),
  })

  client.connect()
  const first = sockets[0] as FakeSocket
  first.open()
  first.receive(serverFrame('connection.ready'))

  const subscribeA = client.subscribe('run-a', 0)
  ack(first)
  await subscribeA
  const subscribeB = client.subscribe('run-b', 0)
  ack(first)
  await subscribeB
  const subscribeTerminal = client.subscribe('run-terminal', 0)
  ack(first)
  await subscribeTerminal
  const subscribeCaught = client.subscribe('run-caught', 9)
  ack(first)
  await subscribeCaught

  first.receive(serverFrame('run.snapshot', {
    task_id: 'run-a', run_id: 'run-a', status: 'running', last_event_sequence: 20,
  }))
  assert.equal(snapshots.length, 1)
  first.receive(serverFrame('run.snapshot', {
    task_id: 'run-terminal', run_id: 'run-terminal', status: 'succeeded', last_event_sequence: 9,
  }))
  first.receive(serverFrame('run.snapshot', {
    task_id: 'run-caught', run_id: 'run-caught', status: 'succeeded', last_event_sequence: 9,
  }))
  assert.equal(snapshots.length, 3)

  first.receive(event('run-a', 1))
  first.receive(event('run-a', 1))
  first.receive(event('run-a', 3))
  assert.deepEqual(received.map((item) => item.sequence), [1])
  assert.deepEqual(gaps, [['run-a', 2, 3]])
  const repair = command(first)
  assert.equal(repair.type, 'run.subscribe')
  assert.deepEqual(repair.payload, { run_id: 'run-a', after: 1 })
  ack(first, repair)

  first.receive(event('run-a', 2))
  first.receive(event('run-a', 3))
  first.receive(event('run-a', 4, 'TASK_COMPLETED'))
  first.receive(event('run-b', 1))
  assert.deepEqual(received.map((item) => `${item.run_id}:${item.sequence}`), [
    'run-a:1', 'run-a:2', 'run-a:3', 'run-a:4', 'run-b:1',
  ])

  const rejectedSubscription = client.subscribe('run-missing', 0)
  const missingCommand = command(first)
  first.receive(serverFrame('command.error', {
    code: 'run_not_found',
    message: 'run does not exist',
    command: 'run.subscribe',
    run_id: 'run-missing',
    retryable: false,
  }, String(missingCommand.request_id)))
  await assert.rejects(rejectedSubscription, /run does not exist/)
  assert.deepEqual(subscriptionErrors, [['run-missing', 'run does not exist']])

  first.close()
  assert.equal(scheduler.nextDelay(), 10)
  scheduler.runNext()
  const second = sockets[1] as FakeSocket
  second.open()
  assert.equal(second.sent.length, 0)
  second.receive(serverFrame('connection.ready'))
  assert.equal(second.sent.length, 2)
  assert.deepEqual(command(second, 0).payload, { run_id: 'run-b', after: 1 })
  assert.deepEqual(command(second, 1).payload, { run_id: 'run-terminal', after: 0 })
  ack(second, command(second, 0))
  second.receive(serverFrame('command.error', {
    code: 'run_not_found',
    message: 'terminal replay is unavailable',
    command: 'run.subscribe',
    run_id: 'run-terminal',
    retryable: false,
  }, String(command(second, 1).request_id)))
  await Promise.resolve()
  await Promise.resolve()
  assert.deepEqual(subscriptionErrors, [
    ['run-missing', 'run does not exist'],
    ['run-terminal', 'terminal replay is unavailable'],
  ])
  client.close()
})

test('heartbeat uses application ping/pong and command errors reject their matching request', async () => {
  const scheduler = new FakeScheduler()
  const socket = new FakeSocket()
  const errors: string[] = []
  let requestSequence = 0
  const client = createPresentationWebSocket({
    url: 'ws://test/api/v1/ppt/ws',
    getAccessToken: () => 'token',
    createSocket: () => socket,
    createRequestId: () => `heartbeat-${++requestSequence}`,
    setTimeout: scheduler.setTimeout,
    clearTimeout: scheduler.clearTimeout,
    heartbeatIntervalMs: 25,
    commandTimeoutMs: 100,
    connectionReadyTimeoutMs: 50,
    onSnapshot: () => {},
    onEvent: () => {},
    onConnectionState: (_state, error) => { if (error) errors.push(error.message) },
  })

  client.connect()
  socket.open()
  assert.equal(scheduler.nextDelay(), 50)
  socket.receive(serverFrame('connection.ready'))
  assert.equal(scheduler.nextDelay(), 25)
  scheduler.runNext()
  const ping = command(socket)
  assert.equal(ping.type, 'ping')
  socket.receive(serverFrame('pong', {}, String(ping.request_id)))
  await Promise.resolve()
  assert.equal(scheduler.nextDelay(), 25)

  const cancellation = client.cancel('run-error')
  const cancelCommand = command(socket)
  socket.receive(serverFrame('command.error', {
    code: 'run_not_found', message: 'run does not exist',
  }, String(cancelCommand.request_id)))
  await assert.rejects(cancellation, /run does not exist/)
  assert.deepEqual(errors, [])

  scheduler.runNext()
  assert.equal(command(socket).type, 'ping')
  scheduler.runNext()
  await Promise.resolve()
  await Promise.resolve()
  assert.equal(socket.closeCode, 4000)
  assert.equal(socket.closeReason, 'heartbeat timeout')
  assert.match(errors.at(-1) ?? '', /等待响应超时/)
  client.close()
})

test('event_cursor_ahead keeps activation pending and replays from zero until a retry ACK arrives', async () => {
  const scheduler = new FakeScheduler()
  const sockets: FakeSocket[] = []
  let requestSequence = 0
  const client = createPresentationWebSocket({
    url: 'ws://test/api/v1/ppt/ws',
    getAccessToken: () => 'token',
    createSocket: () => {
      const socket = new FakeSocket()
      sockets.push(socket)
      return socket
    },
    createRequestId: () => `retry-${++requestSequence}`,
    setTimeout: scheduler.setTimeout,
    clearTimeout: scheduler.clearTimeout,
    random: () => 0,
    reconnectBaseDelayMs: 10,
    onSnapshot: () => {},
    onEvent: () => {},
    onConnectionState: () => {},
  })

  client.connect()
  const first = sockets[0] as FakeSocket
  first.open()
  first.receive(serverFrame('connection.ready'))
  const subscription = client.subscribe('run-retry', 5)
  let activated = false
  void subscription.then(() => { activated = true })
  const subscribeCommand = command(first)
  first.receive(serverFrame('command.error', {
    code: 'event_cursor_ahead',
    message: 'cursor is ahead',
    command: 'run.subscribe',
    run_id: 'run-retry',
    retryable: true,
    last_event_sequence: 2,
  }, String(subscribeCommand.request_id)))
  await Promise.resolve()
  await Promise.resolve()
  assert.equal(activated, false)
  assert.equal(first.closeCode, 4001)
  assert.equal(first.closeReason, 'retryable subscribe error')

  assert.equal(scheduler.nextDelay(), 10)
  scheduler.runNext()
  const second = sockets[1] as FakeSocket
  second.open()
  assert.equal(second.sent.length, 0)
  second.receive(serverFrame('connection.ready'))
  assert.deepEqual(command(second).payload, { run_id: 'run-retry', after: 0 })
  ack(second)
  await subscription
  assert.equal(activated, true)
  client.close()
})

test('connection.ready timeout closes the half-open socket and preserves activation through reconnect', async () => {
  const scheduler = new FakeScheduler()
  const sockets: FakeSocket[] = []
  const errors: string[] = []
  let requestSequence = 0
  const client = createPresentationWebSocket({
    url: 'ws://test/api/v1/ppt/ws',
    getAccessToken: () => 'token',
    createSocket: () => {
      const socket = new FakeSocket()
      sockets.push(socket)
      return socket
    },
    createRequestId: () => `ready-${++requestSequence}`,
    setTimeout: scheduler.setTimeout,
    clearTimeout: scheduler.clearTimeout,
    random: () => 0,
    reconnectBaseDelayMs: 10,
    connectionReadyTimeoutMs: 30,
    onSnapshot: () => {},
    onEvent: () => {},
    onConnectionState: (_state, error) => { if (error) errors.push(error.message) },
  })

  client.connect()
  const first = sockets[0] as FakeSocket
  const activation = client.subscribe('run-half-open', 4)
  let activated = false
  void activation.then(() => { activated = true })
  first.open()
  assert.equal(first.sent.length, 0)
  assert.equal(scheduler.nextDelay(), 30)
  scheduler.runNext()
  assert.equal(first.closeCode, 4002)
  assert.equal(first.closeReason, 'connection ready timeout')
  assert.match(errors[0] ?? '', /connection\.ready 超时/)
  assert.equal(activated, false)

  assert.equal(scheduler.nextDelay(), 10)
  scheduler.runNext()
  const second = sockets[1] as FakeSocket
  second.open()
  second.receive(serverFrame('connection.ready'))
  assert.deepEqual(command(second).payload, { run_id: 'run-half-open', after: 4 })
  ack(second)
  await activation
  assert.equal(activated, true)
  client.close()
})
