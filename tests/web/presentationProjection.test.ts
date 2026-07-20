import assert from 'node:assert/strict'
import test from 'node:test'

import {
  collectPagePreviews,
  findPptResource,
  normalizeTaskEvent,
  progressLabel,
  resolveResourceUrl,
} from '../../apps/web/src/features/presentations/lib/taskProjection.ts'
import type { PptAgentEvent } from '../../apps/web/src/features/presentations/types.ts'

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
