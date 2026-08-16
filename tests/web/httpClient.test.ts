import assert from 'node:assert/strict'
import test from 'node:test'

import { createPresentationApi, toTaskFormData } from '../../apps/web/src/features/presentations/api.ts'
import { ApiError, createHttpClient, toApiUrl } from '../../apps/web/src/shared/api/httpClient.ts'
import type { SessionState } from '../../apps/web/src/types.ts'

function client(authenticated = false) {
  const session: SessionState | null = authenticated
    ? {
        access_token: 'test-access-token',
        refresh_token: 'test-refresh-token',
        token_type: 'Bearer',
        session_id: 'test-session',
        device: 'test',
        access_expires_at: '',
        refresh_expires_at: '',
      }
    : null
  return createHttpClient({
    getSession: () => session,
    setSession: () => undefined,
    onUnauthorized: () => undefined,
    onLog: () => undefined,
  })
}

test('canonical URLs retain the /api/v1 prefix and success envelopes are unwrapped', async (context) => {
  let requestedUrl = ''
  context.mock.method(globalThis, 'fetch', async (input) => {
    requestedUrl = String(input)
    return new Response(JSON.stringify({ data: { status: 'ok' }, meta: { request_id: 'request-1' } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })

  assert.equal(toApiUrl('/health'), '/api/v1/health')
  assert.deepEqual(await client().request('/health'), { status: 'ok' })
  assert.equal(requestedUrl, '/api/v1/health')
})

test('structured API errors preserve code, details, request id, and status', async (context) => {
  context.mock.method(globalThis, 'fetch', async () =>
    new Response(
      JSON.stringify({
        error: {
          code: 'invalid_request',
          message: 'invalid payload',
          details: [{ field: 'name', reason: 'required' }],
          request_id: 'request-2',
        },
      }),
      { status: 400, headers: { 'Content-Type': 'application/json' } },
    ),
  )

  await assert.rejects(client().request('/model-configs'), (error: unknown) => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.status, 400)
    assert.equal(error.code, 'invalid_request')
    assert.equal(error.requestId, 'request-2')
    assert.deepEqual(error.details, [{ field: 'name', reason: 'required' }])
    return true
  })
})

test('text SSE and downloads remain raw response channels', async (context) => {
  const responses = [
    new Response('event: progress\ndata: {"step":1}\n\n', {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    }),
    new Response('binary-content', {
      status: 200,
      headers: { 'Content-Disposition': "attachment; filename*=UTF-8''deck.pptx" },
    }),
  ]
  context.mock.method(globalThis, 'fetch', async () => responses.shift() as Response)

  const stream = await client().openStream('/text-generations', { method: 'POST' })
  assert.equal(await new Response(stream).text(), 'event: progress\ndata: {"step":1}\n\n')

  const download = await client().download('/ppt/tasks/task-1/file')
  assert.equal(download.filename, 'deck.pptx')
  assert.equal(await download.blob.text(), 'binary-content')
})

test('canonical PPT task cancellation uses POST and unwraps the response', async (context) => {
  let requestedPath = ''
  let requestedMethod = ''
  context.mock.method(globalThis, 'fetch', async (input, init) => {
    requestedPath = String(input)
    requestedMethod = init?.method ?? ''
    return new Response(JSON.stringify({ data: { task_id: 'task-42', status: 'cancelled' }, meta: { request_id: 'request-3' } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })

  assert.deepEqual(
    await client().request('/ppt/tasks/task-42/cancel', { method: 'POST' }),
    { task_id: 'task-42', status: 'cancelled' },
  )
  assert.equal(requestedPath, '/api/v1/ppt/tasks/task-42/cancel')
  assert.equal(requestedMethod, 'POST')
})

test('presentation API deletes a task with the canonical DELETE route', async (context) => {
  let requestedPath = ''
  let requestedMethod = ''
  context.mock.method(globalThis, 'fetch', async (input, init) => {
    requestedPath = String(input)
    requestedMethod = init?.method ?? ''
    return new Response(JSON.stringify({ data: { task_id: 'task-42' }, meta: { request_id: 'request-delete' } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })

  await createPresentationApi(client(true)).deleteTask('task-42')
  assert.equal(requestedPath, '/api/v1/ppt/tasks/task-42')
  assert.equal(requestedMethod, 'DELETE')
})

test('presentation API lists tasks with filters and submits run correlation ids', async (context) => {
  const requestedUrls: string[] = []
  const requestedBodies: FormData[] = []
  context.mock.method(globalThis, 'fetch', async (input, init) => {
    requestedUrls.push(String(input))
    if (init?.body instanceof FormData) requestedBodies.push(init.body)
    const data = init?.method === 'POST'
      ? { task_id: 'task-1', status: 'running' }
      : []
    return new Response(JSON.stringify({ data, meta: { request_id: 'request-ppt' } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })

  const api = createPresentationApi(client(true))
  assert.deepEqual(await api.listTasks?.({ threadId: 'thread / 1', limit: 20 }), [])
  assert.equal(requestedUrls[0], '/api/v1/ppt/tasks?thread_id=thread+%2F+1&limit=20')

  const payload = {
    agent_config_id: 7,
    name: '季度经营复盘',
    description: '并行生成一份 PPT',
    thread_id: 'thread-1',
    client_request_id: 'request-1',
  }
  await api.createTask(payload)
  assert.equal(requestedUrls[1], '/api/v1/ppt/tasks')
  assert.equal(requestedBodies.length, 1)

  const form = requestedBodies[0] ?? toTaskFormData(payload)
  assert.equal(form.get('agent_config_id'), '7')
  assert.equal(form.get('name'), '季度经营复盘')
  assert.equal(form.get('description'), '并行生成一份 PPT')
  assert.equal(form.get('thread_id'), 'thread-1')
  assert.equal(form.get('client_request_id'), 'request-1')
  assert.equal(form.has('language'), false)
  assert.equal(form.has('target_page_count'), false)
  assert.equal(form.has('expected_slide_count'), false)
  assert.equal(form.has('aspect_ratio'), false)
  assert.equal(form.has('template_id'), false)
})
