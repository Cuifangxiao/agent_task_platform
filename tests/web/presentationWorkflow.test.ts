import assert from 'node:assert/strict'
import test from 'node:test'

import {
  pptTaskArtifactPath,
  pptTaskPath,
  pptTasksListPath,
} from '../../apps/web/src/features/presentations/lib/taskRoutes.ts'
import {
  eventBelongsToTask,
  hasAppliedTaskSnapshot,
  isCancelledTaskEvent,
  isRunningTask,
  isTerminalTask,
  projectTaskEvent,
  shouldAcceptTaskEvent,
  stageLabel,
  taskEventIdentity,
  taskStatusForEvent,
  taskSnapshotWatermark,
} from '../../apps/web/src/features/presentations/lib/taskProjection.ts'
import {
  createPresentationId,
  createPresentationSubmissionAttempt,
  validatePresentationName,
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

test('replayed run-event sequences are rejected while a newer event advances the cursor', () => {
  const seen = new Set([3, 4])
  assert.equal(shouldAcceptTaskEvent(4, seen, 4), false)
  assert.equal(shouldAcceptTaskEvent(4, seen, 3), false)
  assert.equal(shouldAcceptTaskEvent(4, seen, 5), true)
})

test('terminal snapshots only converge after the local event cursor reaches their watermark', () => {
  const terminal = { task_id: 'run-1', status: 'succeeded', last_event_sequence: 9 } as PptTask
  assert.equal(taskSnapshotWatermark(terminal), 9)
  assert.equal(hasAppliedTaskSnapshot(8, terminal), false)
  assert.equal(hasAppliedTaskSnapshot(9, terminal), true)
  assert.equal(hasAppliedTaskSnapshot(10, terminal), true)
  assert.equal(hasAppliedTaskSnapshot(0, { task_id: 'legacy', status: 'succeeded' }), false)
})

test('event ids deduplicate each run and canonical task/run aliases must agree', () => {
  const seen = new Set(['event-4'])
  assert.equal(shouldAcceptTaskEvent(3, seen, { sequence: 4, event_id: 'event-4' }), false)
  assert.equal(shouldAcceptTaskEvent(3, seen, { sequence: 4, event_id: 'event-5' }), true)
  assert.equal(taskEventIdentity({ sequence: 5 }), 'sequence:5')
  assert.equal(
    eventBelongsToTask(
      { task_id: 'legacy-task-id', run_id: 'run-1' },
      { task_id: 'run-1', run_id: 'run-1' },
    ),
    false,
  )
  assert.equal(
    eventBelongsToTask(
      { task_id: 'run-1', run_id: 'run-1' },
      { task_id: 'run-1', run_id: 'run-1' },
    ),
    true,
  )
})

test('HTTP task routes cover list, snapshots, deletion, and artifacts', () => {
  assert.equal(pptTasksListPath(), '/ppt/tasks')
  assert.equal(
    pptTasksListPath({ threadId: 'thread / 1', limit: 20.8 }),
    '/ppt/tasks?thread_id=thread+%2F+1&limit=20',
  )
  assert.equal(pptTaskPath('task/1'), '/ppt/tasks/task%2F1')
  assert.equal(pptTaskArtifactPath('task-1', 'previews/page 1.png'), '/ppt/tasks/task-1/artifacts/previews/page%201.png')
})

test('new PPT submissions get RFC 4122 ids even without crypto.randomUUID', () => {
  const generated = createPresentationId(null)
  assert.match(generated, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  assert.notEqual(createPresentationId(), createPresentationId())
})

test('a failed identical create attempt reuses ids while changed input gets fresh ids', () => {
  let sequence = 0
  const createId = () => `id-${++sequence}`
  const input = {
    agentConfigId: 7,
    name: '季度汇报',
    description: '生成一份季度汇报',
    sourceFiles: [{ name: 'metrics.csv', size: 42, type: 'text/csv', lastModified: 123 }],
  }
  const first = createPresentationSubmissionAttempt(input, null, createId)
  const identicalRetry = createPresentationSubmissionAttempt({
    ...input,
    sourceFiles: [{ ...input.sourceFiles[0] }],
  }, first, createId)
  assert.equal(identicalRetry, first)
  assert.deepEqual(
    { threadId: identicalRetry.threadId, clientRequestId: identicalRetry.clientRequestId },
    { threadId: 'id-1', clientRequestId: 'id-2' },
  )

  const changed = createPresentationSubmissionAttempt({
    ...input,
    name: '年度汇报',
    description: '生成一份年度汇报',
  }, first, createId)
  assert.deepEqual(
    { threadId: changed.threadId, clientRequestId: changed.clientRequestId },
    { threadId: 'id-3', clientRequestId: 'id-4' },
  )
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

test('presentation names are optional and limited to 120 Unicode characters', () => {
  assert.equal(validatePresentationName(''), '')
  assert.equal(validatePresentationName('季度复盘'), '')
  assert.equal(validatePresentationName('🙂'.repeat(120)), '')
  assert.match(validatePresentationName('🙂'.repeat(121)), /120/)
})
