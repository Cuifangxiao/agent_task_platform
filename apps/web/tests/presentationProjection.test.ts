import assert from 'node:assert/strict'
import test from 'node:test'

import { usePresentationMessages } from '../src/features/presentations/composables/usePresentationMessages.ts'
import { normalizeStoredMessage, normalizeStyleOptions } from '../src/features/presentations/lib/conversationProjection.ts'
import { extractStyleOptions, projectStreamEvent } from '../src/features/presentations/lib/eventProjection.ts'

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
      svg: 'slides/3.svg',
      status: 'ready',
    },
  })

  assert.equal(projection.step.stage, 'designer')
  assert.equal(projection.step.tone, 'done')
  assert.equal(projection.step.state, 'done')
  assert.equal(projection.slideUpdate?.index, 3)
  assert.deepEqual(projection.slideUpdate?.bullets, ['API', 'Runtime'])
  assert.equal(projection.slideUpdate?.previewSvg, 'slides/3.svg')
})

test('progress stages record elapsed time and only the current stage stays live', () => {
  const store = usePresentationMessages(async () => undefined)
  store.createStatusMessage('Working', 'Please wait')

  store.applyStreamChunk({ event_type: 'stage_started', stage: 'outline', message: 'Planning' }, 1_000)
  store.applyStreamChunk({ event_type: 'stage_progress', stage: 'outline', message: 'Writing outline' }, 2_500)
  store.applyStreamChunk({ event_type: 'stage_started', stage: 'writer', message: 'Writing slides' }, 4_000)

  const status = store.messages.value.find((message) => message.kind === 'status')
  assert.equal(status?.kind, 'status')
  if (status?.kind !== 'status') return
  assert.equal(status.steps.length, 2)
  assert.equal(status.steps[0]?.message, 'Writing outline')
  assert.equal(status.steps[0]?.state, 'done')
  assert.equal(status.steps[0]?.startedAt, 1_000)
  assert.equal(status.steps[0]?.completedAt, 4_000)
  assert.equal(status.steps[1]?.state, 'running')
  assert.equal(status.steps[1]?.startedAt, 4_000)
  assert.equal(status.steps[1]?.completedAt, undefined)

  store.applyStreamChunk({ event_type: 'done', stage: 'writer', done: true, message: 'Finished' }, 6_800)
  assert.equal(status.steps[1]?.state, 'done')
  assert.equal(status.steps[1]?.completedAt, 6_800)
})

test('strategist style directions retain template status and all execution confirmations', () => {
  const confirmations = {
    canvas: 'PPT 16:9 / 1280×720',
    page_count: '10 页；按已确认叙事逐页执行',
    audience: '管理层；目标：支持决策',
    style: '咨询汇报 + 信息可视化',
    colors: '背景 #F8FAFC / 主色 #0F766E',
    icons: '单一线性图标语言',
    typography: '标题 32px，正文 18px',
    images: '仅使用已上传素材',
  }
  const [option] = normalizeStyleOptions([
    {
      style_id: 'direction_1',
      name: 'Consulting Signal',
      communication_mode: '咨询汇报',
      visual_style: '信息可视化',
      template_id: 'consulting_signal',
      template_mode: 'layout_pack',
      template_summary: '高密度决策型版式模板',
      rationale: '突出结论、证据与行动项。',
      confirmations,
      layout_principles: ['一页一结论', '数据优先', '避免重复卡片'],
      image_strategy: '优先使用图表和结构图，不虚构照片。',
      tokens_summary: '#F8FAFC / #0F766E / Aptos',
    },
  ])

  assert.equal(option?.templateId, 'consulting_signal')
  assert.equal(option?.templateMode, 'layout_pack')
  assert.equal(option?.communicationMode, '咨询汇报')
  assert.equal(option?.visualStyle, '信息可视化')
  assert.equal(option?.rationale, '突出结论、证据与行动项。')
  assert.deepEqual(option?.confirmations, confirmations)
  assert.equal(Object.keys(option?.confirmations ?? {}).length, 8)
  assert.deepEqual(option?.layoutPrinciples, ['一页一结论', '数据优先', '避免重复卡片'])
  assert.equal(option?.imageStrategy, '优先使用图表和结构图，不虚构照片。')
})

test('style checkpoint extraction supports the public snake-case event contract', () => {
  const options = extractStyleOptions({
    event_type: 'style_options',
    payload: {
      style_options: [
        {
          style_id: 'direction_free',
          name: 'Editorial Freeform',
          template_id: 'editorial_freeform',
          template_mode: 'free_design',
          confirmations: { canvas: '16:9' },
          layout_principles: ['留白优先'],
        },
      ],
    },
  })

  assert.equal(options[0]?.styleId, 'direction_free')
  assert.equal(options[0]?.templateMode, 'free_design')
  assert.deepEqual(options[0]?.confirmations, { canvas: '16:9' })
  assert.deepEqual(options[0]?.layoutPrinciples, ['留白优先'])
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

test('a regenerated artifact refreshes the existing result card', () => {
  const store = usePresentationMessages(async () => undefined)
  store.createStatusMessage('Working', 'Please wait')
  store.applyStreamChunk({
    request_id: 'request-1', event_type: 'artifact', stage: 'artifact',
    payload: { filename: 'deck-v1.pptx', quality_status: 'needs_review', warnings: ['old'], degraded: true },
  })
  store.applyStreamChunk({
    request_id: 'request-1', event_type: 'artifact', stage: 'artifact',
    payload: { filename: 'deck-v2.pptx', quality_status: 'success', warnings: [], degraded: false },
  })

  const results = store.messages.value.filter((message) => message.kind === 'result')
  assert.equal(results.length, 1)
  const result = results[0]
  assert.equal(result?.kind, 'result')
  if (result?.kind !== 'result') return
  assert.equal(result.fileName, 'deck-v2.pptx')
  assert.equal(result.qualityStatus, 'success')
  assert.equal(result.degraded, false)
  assert.deepEqual(result.warnings, [])
})
