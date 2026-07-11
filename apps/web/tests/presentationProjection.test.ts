import assert from 'node:assert/strict'
import test from 'node:test'

import { usePresentationMessages } from '../src/features/presentations/composables/usePresentationMessages.ts'
import { normalizeStoredMessage } from '../src/features/presentations/lib/conversationProjection.ts'
import { projectStreamEvent } from '../src/features/presentations/lib/eventProjection.ts'

test('stored conversation messages are normalized into typed presentation messages', () => {
  const message = normalizeStoredMessage(
    {
      id: 7,
      kind: 'status',
      title: 'Building',
      state: 'running',
      steps: [{ id: 2, label: 'Slides', stage: 'slides', tone: 'done' }],
      slides: [{ index: 2, title: 'Second' }, { index: 1, title: 'First', status: 'ok' }],
    },
    () => 99,
  )

  assert.equal(message.kind, 'status')
  if (message.kind !== 'status') return
  assert.equal(message.steps[0]?.stage, 'writer')
  assert.deepEqual(message.slides.map((slide) => slide.index), [1, 2])
  assert.equal(message.slides[0]?.status, 'reviewed')
})

test('SSE chunks project to a status step and slide patch without touching Vue state', () => {
  const projection = projectStreamEvent({
    event_type: 'slide_ready',
    stage: 'designer',
    stage_status: 'done',
    message: 'Preview ready',
    payload: {
      index: 3,
      title: 'Architecture',
      bullets: ['API', 'Runtime'],
      png: 'slides/3.png',
      status: 'ready',
    },
  })

  assert.equal(projection.step.stage, 'designer')
  assert.equal(projection.step.tone, 'done')
  assert.equal(projection.slideUpdate?.index, 3)
  assert.deepEqual(projection.slideUpdate?.bullets, ['API', 'Runtime'])
})

test('message reducer projects outline and artifact events into chat messages', () => {
  const store = usePresentationMessages(async () => undefined)
  store.createStatusMessage('Working', 'Please wait')
  store.applyStreamChunk({
    request_id: 'request-1',
    event_type: 'outline',
    outline_markdown: '# Deck\n\n- Slide one',
    stage: 'outline_review',
  })
  store.applyStreamChunk({
    request_id: 'request-1',
    event_type: 'artifact',
    stage: 'artifact',
    payload: { filename: 'deck.pptx', quality_status: 'success' },
  })

  assert.ok(store.messages.value.some((message) => message.kind === 'outline'))
  const result = store.messages.value.find((message) => message.kind === 'result')
  assert.equal(result?.kind === 'result' ? result.fileName : '', 'deck.pptx')
})

test('latest validation report replaces resolved slide warnings and uses review status', () => {
  const store = usePresentationMessages(async () => undefined)
  store.createStatusMessage('Working', 'Please wait')
  store.applyStreamChunk({
    request_id: 'request-1',
    event_type: 'slide_ready',
    stage: 'writer',
    payload: { index: 1, title: 'Transformer', status: 'ready' },
  })
  store.applyStreamChunk({
    request_id: 'request-1',
    event_type: 'validation_report',
    stage: 'validate',
    payload: {
      issues: [{ page: 1, severity: 'must_fix', description: 'BERT and GPT need a source.' }],
    },
  })
  store.applyStreamChunk({
    request_id: 'request-1',
    event_type: 'validation_report',
    stage: 'validate',
    payload: {
      issues: [{ page: 1, severity: 'must_fix', description: 'Current overflow warning.' }],
    },
  })

  const status = store.messages.value.find((message) => message.kind === 'status')
  assert.equal(status?.kind, 'status')
  if (status?.kind !== 'status') return
  assert.equal(status.slides[0]?.status, 'warning')
  assert.deepEqual(status.slides[0]?.warnings, ['Current overflow warning.'])

  store.applyStreamChunk({
    request_id: 'request-1',
    event_type: 'validation_report',
    stage: 'validate',
    payload: { issues: [] },
  })
  assert.equal(status.slides[0]?.status, 'reviewed')
  assert.deepEqual(status.slides[0]?.warnings, [])
})
