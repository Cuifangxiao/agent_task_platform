import assert from 'node:assert/strict'
import test from 'node:test'

import { ApiError, createHttpClient, toApiUrl } from '../src/shared/api/httpClient.ts'

function client() {
  return createHttpClient({
    getSession: () => null,
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

test('SSE and downloads remain raw response channels', async (context) => {
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

  const stream = await client().openStream('/presentations/jobs/job-1/events')
  assert.equal(await new Response(stream).text(), 'event: progress\ndata: {"step":1}\n\n')

  const download = await client().download('/presentations/artifacts/request-1')
  assert.equal(download.filename, 'deck.pptx')
  assert.equal(await download.blob.text(), 'binary-content')
})

test('canonical presentation conversation deletion uses DELETE and unwraps the response', async (context) => {
  let requestedPath = ''
  let requestedMethod = ''
  context.mock.method(globalThis, 'fetch', async (input, init) => {
    requestedPath = String(input)
    requestedMethod = init?.method ?? ''
    return new Response(JSON.stringify({ data: { deleted: true, id: 42 }, meta: { request_id: 'request-3' } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })

  assert.deepEqual(
    await client().request('/presentations/conversations/42', { method: 'DELETE' }),
    { deleted: true, id: 42 },
  )
  assert.equal(requestedPath, '/api/v1/presentations/conversations/42')
  assert.equal(requestedMethod, 'DELETE')
})
