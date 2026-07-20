import assert from 'node:assert/strict'
import test from 'node:test'

import {
  compactTaskEvents,
  collectPagePreviews,
  findPptResource,
  normalizeTaskEvent,
  overallTaskProgress,
  progressLabel,
  projectTaskPhases,
  resolveResourceUrl,
} from '../../apps/web/src/features/presentations/lib/taskProjection.ts'
import {
  presentationConversationId,
  projectPresentationConversation,
} from '../../apps/web/src/features/presentations/lib/conversationProjection.ts'
import type { PptAgentEvent, PptTask } from '../../apps/web/src/features/presentations/types.ts'

test('a typed transport event becomes one canonical task event', () => {
  const event = normalizeTaskEvent({
    id: 12,
    event: 'STEP_PROGRESS',
    data: {
      schema_version: '1',
      event_id: 'event-12',
      thread_id: 'thread-123',
      run_id: 'run-123',
      task_id: 'run-123',
      sequence: 99,
      type: 'STEP_PROGRESS',
      stage: 'GENERATING_PAGES',
      message: '正在生成第 3/10 页',
      progress: { current: 3, total: 10, percent: 30, unit: 'page' },
      page: { page_index: 3, page_count: 10, title: '系统架构' },
    },
  })

  assert.equal(event?.sequence, 12)
  assert.equal(event?.schema_version, '1')
  assert.equal(event?.event_id, 'event-12')
  assert.equal(event?.thread_id, 'thread-123')
  assert.equal(event?.run_id, 'run-123')
  assert.equal(event?.task_id, 'run-123')
  assert.equal(event?.type, 'STEP_PROGRESS')
  assert.equal(event?.page?.page_index, 3)
  assert.equal(progressLabel(event as PptAgentEvent), '3/10 页')
})

test('legacy events receive a stable fallback event id and run_id can identify the task', () => {
  const legacy = normalizeTaskEvent({
    id: 3,
    event: 'TASK_STARTED',
    data: { task_id: 'task-legacy', sequence: 3, message: 'started' },
  })
  const runOnly = normalizeTaskEvent({
    id: 1,
    event: 'TASK_STARTED',
    data: { run_id: 'run-only', sequence: 1, message: 'started' },
  })

  assert.equal(legacy?.event_id, 'task-legacy:3')
  assert.equal(runOnly?.task_id, 'run-only')
  assert.equal(normalizeTaskEvent({
    id: 4,
    event: 'TASK_STARTED',
    data: { task_id: 'task-4', run_id: 'run-4', sequence: 4 },
  }), null)
})

test('malformed stream data is rejected instead of entering UI state', () => {
  assert.equal(normalizeTaskEvent({ event: 'message', data: 'not-json' }), null)
  assert.equal(normalizeTaskEvent({ event: 'TASK_STARTED', data: { task_id: 'task-1' } }), null)
  assert.equal(normalizeTaskEvent({ id: 1, event: 'unknown', data: { task_id: 'task-1' } }), null)
})

test('PAGE_PREVIEW resources are deduplicated and sorted by page index', () => {
  const events: PptAgentEvent[] = [
    {
      task_id: 'task-1', sequence: 2, type: 'ARTIFACT_READY', stage: 'RENDERING', message: '第二页预览',
      page: { page_index: 2, title: '第二页' },
      resources: [{ resource_id: 'page-2', type: 'PAGE_PREVIEW', name: '2.png', url: '/ppt/tasks/task-1/previews/2' }],
    },
    {
      task_id: 'task-1', sequence: 3, type: 'ARTIFACT_READY', stage: 'RENDERING', message: '第一页预览',
      page: { page_index: 1, title: '第一页' },
      resources: [{ resource_id: 'page-1', type: 'PAGE_PREVIEW', name: '1.png', url: '/ppt/tasks/task-1/previews/1' }],
    },
    {
      task_id: 'task-1', sequence: 4, type: 'ARTIFACT_READY', stage: 'RENDERING', message: '第二页预览更新',
      page: { page_index: 2, title: '第二页' },
      resources: [{ resource_id: 'page-2', type: 'PAGE_PREVIEW', name: '2.png', url: '/ppt/tasks/task-1/previews/2' }],
    },
  ]

  const previews = collectPagePreviews(events)
  assert.equal(previews.length, 2)
  assert.deepEqual(previews.map((item) => item.page?.page_index), [1, 2])
  assert.equal(previews[1]?.sequence, 4)
})

test('the final PPT resource is read from the terminal event', () => {
  const ppt = { resource_id: 'ppt-1', type: 'PPTX', name: 'deck.pptx', url: '/ppt/tasks/task-1/file' }
  const events: PptAgentEvent[] = [
    { task_id: 'task-1', sequence: 1, type: 'TASK_STARTED', stage: 'PREPARING', message: '开始' },
    {
      task_id: 'task-1', sequence: 2, type: 'TASK_COMPLETED', stage: 'FINISHED', message: '完成',
      resources: [ppt], final_result: { ppt_file: ppt, slide_count: 10 },
    },
  ]

  assert.deepEqual(findPptResource(events), ppt)
  assert.equal(resolveResourceUrl('/ppt/tasks/task-1/previews/1'), '/api/v1/ppt/tasks/task-1/previews/1')
  assert.equal(resolveResourceUrl('https://files.example.com/1.png'), 'https://files.example.com/1.png')
})

test('raw stages are projected into a monotonic six-phase user journey', () => {
  const task: PptTask = {
    task_id: 'task-1', status: 'running', stage: 'GENERATING_PAGES', message: '第三页生成中',
  }
  const events: PptAgentEvent[] = [
    { task_id: 'task-1', sequence: 1, type: 'TASK_STARTED', stage: 'PREPARING', message: '开始' },
    { task_id: 'task-1', sequence: 2, type: 'STEP_COMPLETED', stage: 'PLANNING', message: '大纲完成' },
    { task_id: 'task-1', sequence: 3, type: 'STEP_COMPLETED', stage: 'GENERATING_ASSETS', message: '设计完成' },
    {
      task_id: 'task-1', sequence: 4, type: 'ARTIFACT_READY', stage: 'RENDERING', message: '第 2 页预览完成',
      page: { page_index: 2, page_count: 8, title: '第二页' },
      progress: { current: 2, total: 8, percent: 25, unit: 'page' },
    },
    {
      task_id: 'task-1', sequence: 5, type: 'STEP_STARTED', stage: 'GENERATING_CONTENT', message: '第 3 页生成中',
      page: { page_index: 3, page_count: 8, title: '第三页' },
    },
  ]

  const phases = projectTaskPhases(events, task)
  assert.deepEqual(phases.map((phase) => phase.status), ['done', 'done', 'done', 'active', 'pending', 'pending'])
  assert.equal(phases[3]?.pageCurrent, 3)
  assert.equal(phases[3]?.pageTotal, 8)
  assert.ok(overallTaskProgress(task, events) > 38)
  assert.ok(overallTaskProgress(task, events) < 90)
})

test('token-level duplicate updates collapse without dropping milestone events', () => {
  const events: PptAgentEvent[] = [
    { task_id: 'task-1', sequence: 1, type: 'STEP_STARTED', stage: 'PLANNING', message: '生成大纲' },
    { task_id: 'task-1', sequence: 2, type: 'STEP_PROGRESS', stage: 'PLANNING', message: 'Streaming outline markdown' },
    { task_id: 'task-1', sequence: 3, type: 'STEP_PROGRESS', stage: 'PLANNING', message: 'Streaming outline markdown' },
    { task_id: 'task-1', sequence: 4, type: 'STEP_PROGRESS', stage: 'PLANNING', message: 'Streaming outline markdown' },
    { task_id: 'task-1', sequence: 5, type: 'STEP_COMPLETED', stage: 'PLANNING', message: '大纲完成' },
  ]

  const compacted = compactTaskEvents(events)
  assert.equal(compacted.length, 3)
  assert.equal(compacted[1]?.repeatCount, 3)
  assert.equal(compacted[1]?.event.sequence, 4)
  assert.equal(compacted[2]?.event.type, 'STEP_COMPLETED')
})

test('phase progress is interpreted inside its phase instead of treating preparation as overall 100%', () => {
  const task: PptTask = {
    task_id: 'task-1', status: 'running', stage: 'PREPARING', message: '资料准备完成',
  }
  const events: PptAgentEvent[] = [{
    task_id: 'task-1', sequence: 1, type: 'STEP_COMPLETED', stage: 'PREPARING', message: '资料准备完成',
    progress: { current: 1, total: 1, percent: 100, unit: 'attachment' },
  }]

  assert.equal(overallTaskProgress(task, events), 8)
})

test('a presentation run projects into one sparse conversation instead of one bubble per event', () => {
  const task: PptTask = {
    task_id: 'run-1',
    run_id: 'run-1',
    thread_id: 'thread-1',
    description: ' 为销售团队制作一份 8 页产品发布方案 ',
    status: 'running',
    stage: 'GENERATING_PAGES',
    created_at: '2026-07-20T10:00:00Z',
    updated_at: '2026-07-20T10:03:00Z',
  }
  const events: PptAgentEvent[] = [
    {
      event_id: 'prepare', task_id: 'run-1', run_id: 'run-1', sequence: 1,
      type: 'STEP_COMPLETED', stage: 'PREPARING', message: 'input ready',
      created_at: '2026-07-20T10:00:10Z',
    },
    ...Array.from({ length: 40 }, (_, index): PptAgentEvent => ({
      event_id: `token-${index}`, task_id: 'run-1', run_id: 'run-1', sequence: index + 2,
      type: 'STEP_PROGRESS', stage: 'PLANNING', message: `outline token ${index}`,
      created_at: '2026-07-20T10:01:00Z',
    })),
    {
      event_id: 'design', task_id: 'run-1', run_id: 'run-1', sequence: 42,
      type: 'STEP_COMPLETED', stage: 'GENERATING_ASSETS', message: 'style ready',
      created_at: '2026-07-20T10:02:00Z',
    },
    {
      event_id: 'page-start', task_id: 'run-1', run_id: 'run-1', sequence: 43,
      type: 'STEP_STARTED', stage: 'GENERATING_PAGES', message: 'page 3 started',
      page: { page_index: 3, page_count: 8, title: '核心价值' },
      progress: { current: 3, total: 8, percent: 38, unit: 'page' },
      created_at: '2026-07-20T10:03:00Z',
    },
    {
      event_id: 'page-preview', task_id: 'run-1', run_id: 'run-1', sequence: 44,
      type: 'ARTIFACT_READY', stage: 'GENERATING_PAGES', message: 'page 3 preview ready',
      page: { page_index: 3, page_count: 8, title: '核心价值' },
      progress: { current: 3, total: 8, percent: 38, unit: 'page' },
      resources: [{
        resource_id: 'preview-3', type: 'PAGE_PREVIEW', name: '3.png', url: '/previews/3.png',
      }],
      created_at: '2026-07-20T10:03:10Z',
    },
    {
      event_id: 'foreign', task_id: 'run-2', run_id: 'run-2', sequence: 100,
      type: 'TASK_FAILED', stage: 'PLANNING', message: 'must not leak into run-1',
      error: { code: 'FAILED', message: 'other task failed' },
    },
  ]

  const conversation = projectPresentationConversation(task, events)

  assert.equal(conversation.id, 'run-1')
  assert.equal(conversation.threadId, 'thread-1')
  assert.equal(conversation.messages.length, 6)
  assert.deepEqual(
    conversation.messages.map((message) => message.kind),
    ['request', 'milestone', 'milestone', 'milestone', 'milestone', 'page_progress'],
  )
  assert.equal(conversation.messages[0]?.content, '为销售团队制作一份 8 页产品发布方案')
  assert.equal(conversation.messages.filter((message) => message.phase === 'plan').length, 1)
  const pageProgress = conversation.messages.find((message) => message.kind === 'page_progress')
  assert.equal(pageProgress?.key, 'run-1:page-progress')
  assert.equal(pageProgress?.content, '第 3/8 页「核心价值」的预览已经就绪。')
  assert.equal(pageProgress?.resources?.[0]?.resource_id, 'preview-3')
})

test('page progress updates preserve the message key and creation time while advancing its latest state', () => {
  const task: PptTask = {
    task_id: 'run-stable', status: 'running', stage: 'GENERATING_PAGES',
    description: '生成季度复盘', created_at: '2026-07-20T11:00:00Z',
  }
  const firstPage: PptAgentEvent = {
    event_id: 'page-1', task_id: 'run-stable', sequence: 10,
    type: 'ARTIFACT_READY', stage: 'GENERATING_PAGES', message: 'page 1 ready',
    page: { page_index: 1, page_count: 6, title: '封面' },
    progress: { current: 1, total: 6, unit: 'page' },
    resources: [{ resource_id: 'preview-1', type: 'PAGE_PREVIEW', name: '1.png', url: '/1.png' }],
    created_at: '2026-07-20T11:01:00Z',
  }
  const nextPage: PptAgentEvent = {
    event_id: 'page-2', task_id: 'run-stable', sequence: 11,
    type: 'STEP_STARTED', stage: 'GENERATING_PAGES', message: 'page 2 started',
    page: { page_index: 2, page_count: 6, title: '经营摘要' },
    progress: { current: 2, total: 6, unit: 'page' },
    created_at: '2026-07-20T11:02:00Z',
  }

  const before = projectPresentationConversation(task, [firstPage])
  const after = projectPresentationConversation(task, [firstPage, nextPage])
  const beforeProgress = before.messages.find((message) => message.kind === 'page_progress')
  const afterProgress = after.messages.find((message) => message.kind === 'page_progress')

  assert.equal(beforeProgress?.key, afterProgress?.key)
  assert.equal(afterProgress?.createdAt, '2026-07-20T11:01:00Z')
  assert.equal(afterProgress?.updatedAt, '2026-07-20T11:02:00Z')
  assert.equal(afterProgress?.updatedSequence, 11)
  assert.equal(afterProgress?.progress?.current, 2)
  assert.match(afterProgress?.content || '', /正在生成第 2\/6 页/)
  assert.equal(afterProgress?.resources?.[0]?.resource_id, 'preview-1')
})

test('terminal success and failure become one stable terminal conversation message', () => {
  const ppt = {
    resource_id: 'deck-1', type: 'PPTX' as const, name: 'launch.pptx', url: '/launch.pptx',
  }
  const succeeded: PptTask = {
    task_id: 'run-success', status: 'succeeded', stage: 'FINISHED', description: '产品发布会',
    result: { ppt_file: ppt, slide_count: 12 }, finished_at: '2026-07-20T12:00:00Z',
  }
  const successConversation = projectPresentationConversation(succeeded, [{
    event_id: 'completed', task_id: 'run-success', sequence: 20,
    type: 'TASK_COMPLETED', stage: 'FINISHED', message: 'done',
    final_result: { ppt_file: ppt, slide_count: 12 }, resources: [ppt],
    created_at: '2026-07-20T12:00:00Z',
  }])
  const completion = successConversation.messages.at(-1)

  assert.equal(completion?.key, 'run-success:terminal')
  assert.equal(completion?.kind, 'completion')
  assert.match(completion?.content || '', /共 12 页/)
  assert.equal(completion?.resources?.length, 1)

  const failed: PptTask = {
    task_id: 'run-failed', status: 'failed', stage: 'PLANNING', description: '失败案例',
    error: { code: 'OUTLINE_INVALID', message: '无法从附件中提取有效内容' },
  }
  const failure = projectPresentationConversation(failed, []).messages.at(-1)
  assert.equal(failure?.key, 'run-failed:terminal')
  assert.equal(failure?.kind, 'error')
  assert.equal(failure?.content, '无法从附件中提取有效内容')
  assert.equal(presentationConversationId({ task_id: 'task-id', run_id: 'run-id' }), 'run-id')
})
