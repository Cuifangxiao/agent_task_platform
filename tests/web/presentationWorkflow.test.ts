import assert from 'node:assert/strict'
import test from 'node:test'

import { pptTaskArtifactPath, pptTaskEventRequest, pptTaskPath } from '../../apps/web/src/features/presentations/lib/taskRoutes.ts'
import {
  isCancelledTaskEvent,
  isRunningTask,
  isTerminalTask,
  projectTaskEvent,
  shouldAcceptTaskEvent,
  stageLabel,
  synchronizeTaskCursor,
  taskStatusForEvent,
} from '../../apps/web/src/features/presentations/lib/taskProjection.ts'
import {
  validatePresentationDescription,
  validateSourceFiles,
} from '../../apps/web/src/features/presentations/lib/presentationUtils.ts'
import type { PptAgentEvent, PptTask } from '../../apps/web/src/features/presentations/types.ts'

test('task lifecycle only exposes running and terminal states', () => {
  assert.equal(isRunningTask({ task_id: 'task-1', status: 'running' }), true)
  assert.equal(isTerminalTask({ task_id: 'task-1', status: 'running' }), false)
  assert.equal(isTerminalTask({ task_id: 'task-1', status: 'succeeded' }), true)
  assert.equal(isTerminalTask({ task_id: 'task-1', status: 'failed' }), true)
  assert.equal(isTerminalTask({ task_id: 'task-1', status: 'cancelled' }), true)
})

test('terminal event types project directly to task status', () => {
  assert.equal(taskStatusForEvent({ type: 'TASK_STARTED' }), 'running')
  assert.equal(taskStatusForEvent({ type: 'STEP_PROGRESS' }), 'running')
  assert.equal(taskStatusForEvent({ type: 'TASK_COMPLETED' }), 'succeeded')
  assert.equal(taskStatusForEvent({ type: 'TASK_FAILED' }), 'failed')
})

test('TASK_CANCELLED failure events project to the cancelled terminal state', () => {
  const event = {
    type: 'TASK_FAILED' as const,
    error: { code: 'TASK_CANCELLED', message: 'cancelled' },
  }
  assert.equal(isCancelledTaskEvent(event), true)
  assert.equal(taskStatusForEvent(event), 'cancelled')
})

test('a buffered non-terminal event cannot overwrite an authoritative terminal snapshot', () => {
  const cancelledTask: PptTask = {
    task_id: 'task-1',
    status: 'cancelled',
    stage: 'GENERATING_PAGES',
    message: 'PPT task cancelled',
    last_event_sequence: 8,
    error: { code: 'TASK_CANCELLED', message: 'PPT task cancelled' },
  }
  const bufferedProgress: PptAgentEvent = {
    task_id: 'task-1',
    sequence: 9,
    type: 'STEP_PROGRESS',
    stage: 'GENERATING_PAGES',
    message: 'stale progress',
    progress: { current: 4, total: 10, percent: 40 },
  }

  const projected = projectTaskEvent(cancelledTask, bufferedProgress)
  assert.equal(projected.status, 'cancelled')
  assert.equal(projected.message, 'PPT task cancelled')
  assert.equal(projected.progress, undefined)
  assert.equal(projected.last_event_sequence, 9)
})

test('task snapshots advance the reconnect cursor without moving it backwards', () => {
  const snapshot = { task_id: 'task-1', status: 'running', last_event_sequence: 8 } as PptTask
  assert.equal(synchronizeTaskCursor(3, snapshot), 8)
  assert.equal(synchronizeTaskCursor(10, snapshot), 10)
  assert.equal(synchronizeTaskCursor(3, { task_id: 'task-1', status: 'running' }), 3)
})

test('replayed SSE sequences are rejected while a newer event advances the cursor', () => {
  const seen = new Set([3, 4])
  assert.equal(shouldAcceptTaskEvent(4, seen, 4), false)
  assert.equal(shouldAcceptTaskEvent(4, seen, 3), false)
  assert.equal(shouldAcceptTaskEvent(4, seen, 5), true)
})

test('SSE reconnect request carries both after and Last-Event-ID', () => {
  assert.equal(pptTaskPath('task/1'), '/ppt/tasks/task%2F1')
  assert.deepEqual(pptTaskEventRequest('task/1', 7), {
    path: '/ppt/tasks/task%2F1/events?after=7',
    headers: { 'Last-Event-ID': '7' },
  })
  assert.deepEqual(pptTaskEventRequest('task-1'), {
    path: '/ppt/tasks/task-1/events',
    headers: undefined,
  })
  assert.equal(pptTaskArtifactPath('task-1', 'previews/page 1.png'), '/ppt/tasks/task-1/artifacts/previews/page%201.png')
})

test('stable pipeline stages have readable labels', () => {
  assert.equal(stageLabel('PREPARING'), '准备输入')
  assert.equal(stageLabel('PLANNING'), '规划演示文稿')
  assert.equal(stageLabel('GENERATING_PAGES'), '生成页面')
  assert.equal(stageLabel('ASSEMBLING_PPT'), '组装 PPT')
  assert.equal(stageLabel('FINISHED'), '生成完成')
})

test('presentation uploads accept safe raster image formats and keep the 20 MB limit', () => {
  assert.equal(validateSourceFiles([{ name: 'hero.webp', size: 1024 } as File]), '')
  assert.equal(validateSourceFiles([{ name: 'photo.jpeg', size: 1024 } as File]), '')
  assert.match(validateSourceFiles([{ name: 'empty.pdf', size: 0 } as File]), /空文件/)
  assert.match(validateSourceFiles([{ name: 'vector.svg', size: 1024 } as File]), /不支持/)
  assert.match(validateSourceFiles([{ name: 'huge.png', size: 20 * 1024 * 1024 + 1 } as File]), /20 MB/)
})

test('presentation descriptions use the same 5000 Unicode character limit as Go', () => {
  assert.match(validatePresentationDescription('   '), /先描述/)
  assert.equal(validatePresentationDescription('🙂'.repeat(5_000)), '')
  assert.match(validatePresentationDescription('🙂'.repeat(5_001)), /5000/)
})
