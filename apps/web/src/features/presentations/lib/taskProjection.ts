import type {
  PptAgentEvent,
  PptEventType,
  PptPagePreview,
  PptResource,
  PptTask,
  PptTaskStatus,
} from '../types'

export interface RawPresentationEvent {
  event?: string
  id?: string | number
  data: unknown
}

const TERMINAL_STATUSES = new Set<PptTaskStatus>(['succeeded', 'failed', 'cancelled'])
const EVENT_TYPES = new Set<PptEventType>([
  'TASK_STARTED',
  'STEP_STARTED',
  'STEP_PROGRESS',
  'STEP_COMPLETED',
  'ARTIFACT_READY',
  'TASK_COMPLETED',
  'TASK_FAILED',
])

export function normalizeTaskEvent(event: RawPresentationEvent): PptAgentEvent | null {
  if (!isRecord(event.data)) return null
  const rawType = String(event.data.type || event.event || '').toUpperCase()
  const type = EVENT_TYPES.has(rawType as PptEventType) ? (rawType as PptEventType) : null
  const rawTaskId = normalizedIdentifier(event.data.task_id)
  const rawRunId = normalizedIdentifier(event.data.run_id)
  if (rawTaskId && rawRunId && rawTaskId !== rawRunId) return null
  const taskId = rawTaskId || rawRunId
  const sequence = Number(event.id ?? event.data.sequence)
  if (!type || !taskId || !Number.isInteger(sequence) || sequence <= 0) return null

  const progress = isRecord(event.data.progress)
    ? {
        current: finiteNumber(event.data.progress.current),
        total: finiteNumber(event.data.progress.total),
        ...(optionalFiniteNumber(event.data.progress.percent) === undefined
          ? {}
          : { percent: optionalFiniteNumber(event.data.progress.percent) }),
        ...(typeof event.data.progress.unit === 'string' ? { unit: event.data.progress.unit } : {}),
      }
    : undefined

  return {
    ...(typeof event.data.schema_version === 'string' || typeof event.data.schema_version === 'number'
      ? { schema_version: event.data.schema_version }
      : {}),
    event_id: normalizedIdentifier(event.data.event_id) || `${taskId}:${sequence}`,
    ...(normalizedIdentifier(event.data.thread_id)
      ? { thread_id: normalizedIdentifier(event.data.thread_id) }
      : {}),
    ...(normalizedIdentifier(event.data.run_id) ? { run_id: normalizedIdentifier(event.data.run_id) } : {}),
    task_id: taskId,
    ...(typeof event.data.channel === 'string' ? { channel: event.data.channel } : {}),
    sequence,
    type,
    message: String(event.data.message || eventLabel(type)),
    ...(typeof event.data.created_at === 'string' ? { created_at: event.data.created_at } : {}),
    ...(typeof event.data.stage === 'string' ? { stage: event.data.stage as PptAgentEvent['stage'] } : {}),
    ...(progress ? { progress } : {}),
    ...(Array.isArray(event.data.resources) ? { resources: event.data.resources as PptResource[] } : {}),
    ...(isRecord(event.data.page)
      ? {
          page: {
            page_index: finiteNumber(event.data.page.page_index),
            ...(typeof event.data.page.page_id === 'string' ? { page_id: event.data.page.page_id } : {}),
            ...(optionalFiniteNumber(event.data.page.page_count) === undefined
              ? {}
              : { page_count: optionalFiniteNumber(event.data.page.page_count) }),
            ...(typeof event.data.page.title === 'string' ? { title: event.data.page.title } : {}),
          },
        }
      : {}),
    ...(isRecord(event.data.error)
      ? {
          error: {
            code: String(event.data.error.code || 'PPT_GENERATION_FAILED'),
            message: String(event.data.error.message || event.data.message || 'PPT 生成失败'),
            ...(typeof event.data.error.retryable === 'boolean'
              ? { retryable: event.data.error.retryable }
              : {}),
          },
        }
      : {}),
    ...(isRecord(event.data.final_result)
      ? { final_result: event.data.final_result as PptAgentEvent['final_result'] }
      : {}),
  }
}

export function taskEventIdentity(event: Pick<PptAgentEvent, 'event_id' | 'sequence'>) {
  return event.event_id?.trim() || `sequence:${event.sequence}`
}

export function shouldAcceptTaskEvent(
  lastSequence: number,
  seen: ReadonlySet<string | number>,
  eventOrSequence: number | Pick<PptAgentEvent, 'event_id' | 'sequence'>,
) {
  const sequence = typeof eventOrSequence === 'number' ? eventOrSequence : eventOrSequence.sequence
  const identity = typeof eventOrSequence === 'number'
    ? eventOrSequence
    : taskEventIdentity(eventOrSequence)
  return Number.isInteger(sequence) && sequence > lastSequence && !seen.has(identity)
}

export function eventBelongsToTask(
  event: Pick<PptAgentEvent, 'task_id' | 'run_id'>,
  task: Pick<PptTask, 'task_id' | 'run_id'>,
) {
  if (event.run_id && event.task_id !== event.run_id) return false
  if (task.run_id && task.task_id !== task.run_id) return false
  return (event.run_id || event.task_id) === (task.run_id || task.task_id)
}

export function isTerminalTask(task: PptTask | null | undefined) {
  return Boolean(task && TERMINAL_STATUSES.has(task.status))
}

export function isRunningTask(task: PptTask | null | undefined) {
  return task?.status === 'running'
}

export function taskSnapshotWatermark(task: Pick<PptTask, 'last_event_sequence'>) {
  const sequence = Number(task.last_event_sequence)
  return Number.isSafeInteger(sequence) && sequence >= 0 ? sequence : null
}

export function hasAppliedTaskSnapshot(
  lastEventSequence: number,
  task: Pick<PptTask, 'last_event_sequence'>,
) {
  const watermark = taskSnapshotWatermark(task)
  return watermark !== null && lastEventSequence >= watermark
}

export function isCancelledTaskEvent(event: Pick<PptAgentEvent, 'type' | 'error'>) {
  return event.type === 'TASK_FAILED' && event.error?.code.trim().toUpperCase() === 'TASK_CANCELLED'
}

export function taskStatusForEvent(event: Pick<PptAgentEvent, 'type' | 'error'>): PptTaskStatus {
  if (event.type === 'TASK_COMPLETED') return 'succeeded'
  if (isCancelledTaskEvent(event)) return 'cancelled'
  if (event.type === 'TASK_FAILED') return 'failed'
  return 'running'
}

export function projectTaskEvent(task: PptTask, event: PptAgentEvent): PptTask {
  const eventStatus = taskStatusForEvent(event)
  const nextSequence = Math.max(task.last_event_sequence ?? 0, event.sequence)

  // A task snapshot can reach us before an already-buffered run event. Keep the
  // authoritative terminal snapshot intact while still advancing the cursor.
  if (isTerminalTask(task) && eventStatus === 'running') {
    return { ...task, last_event_sequence: nextSequence }
  }

  return {
    ...task,
    ...(event.schema_version !== undefined ? { schema_version: event.schema_version } : {}),
    ...(event.event_id ? { event_id: event.event_id } : {}),
    ...(event.thread_id ? { thread_id: event.thread_id } : {}),
    ...(event.run_id ? { run_id: event.run_id } : {}),
    status: eventStatus,
    ...(event.stage ? { stage: event.stage } : {}),
    message: event.message,
    ...(event.progress ? { progress: event.progress } : {}),
    ...(event.final_result ? { result: event.final_result } : {}),
    ...(event.error ? { error: event.error } : {}),
    last_event_sequence: nextSequence,
  }
}

export function collectPagePreviews(events: PptAgentEvent[]): PptPagePreview[] {
  const previews = new Map<string, PptPagePreview>()
  for (const event of events) {
    for (const resource of event.resources ?? []) {
      if (resource.type.toUpperCase() !== 'PAGE_PREVIEW' || (!resource.url && !resource.relative_path)) continue
      previews.set(resource.resource_id, { resource, page: event.page, sequence: event.sequence })
    }
  }
  return [...previews.values()].sort((left, right) => {
    const leftPage = left.page?.page_index ?? Number.MAX_SAFE_INTEGER
    const rightPage = right.page?.page_index ?? Number.MAX_SAFE_INTEGER
    return leftPage - rightPage || left.sequence - right.sequence
  })
}

export function findPptResource(events: PptAgentEvent[]) {
  for (let eventIndex = events.length - 1; eventIndex >= 0; eventIndex -= 1) {
    const event = events[eventIndex]
    if (event?.final_result?.ppt_file) return event.final_result.ppt_file
    const resource = [...(event?.resources ?? [])]
      .reverse()
      .find((item) => item.type.toUpperCase() === 'PPTX')
    if (resource) return resource
  }
  return null
}

export function stageLabel(stage: string | undefined) {
  switch (stage) {
    case 'PREPARING':
      return '准备输入'
    case 'PLANNING':
      return '规划演示文稿'
    case 'GENERATING_CONTENT':
      return '生成内容'
    case 'GENERATING_ASSETS':
      return '生成素材'
    case 'GENERATING_PAGES':
      return '生成页面'
    case 'RENDERING':
      return '渲染页面'
    case 'ASSEMBLING_PPT':
      return '组装 PPT'
    case 'FINISHED':
      return '生成完成'
    default:
      return stage || '等待开始'
  }
}

export function eventLabel(type: PptEventType) {
  switch (type) {
    case 'TASK_STARTED':
      return '任务已开始'
    case 'STEP_STARTED':
      return '进入新阶段'
    case 'STEP_PROGRESS':
      return '生成进度更新'
    case 'STEP_COMPLETED':
      return '阶段已完成'
    case 'ARTIFACT_READY':
      return '产物已就绪'
    case 'TASK_COMPLETED':
      return 'PPT 生成完成'
    case 'TASK_FAILED':
      return 'PPT 生成失败'
  }
}

export function taskStatusLabel(status: PptTaskStatus | undefined) {
  switch (status) {
    case 'running':
      return '生成中'
    case 'succeeded':
      return '已完成'
    case 'failed':
      return '失败'
    case 'cancelled':
      return '已取消'
    default:
      return '未开始'
  }
}

export function progressLabel(event: PptAgentEvent) {
  const progress = event.progress
  if (!progress || progress.total <= 0) return ''
  const unit = progress.unit === 'page' ? '页' : progress.unit || '项'
  return `${progress.current}/${progress.total} ${unit}`
}

export function resolveResourceUrl(url: string) {
  if (/^(https?:|data:|blob:)/i.test(url) || url.startsWith('/api/')) return url
  const path = url.startsWith('/') ? url : `/${url}`
  return `/api/v1${path}`
}

function finiteNumber(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? number : 0
}

function optionalFiniteNumber(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? number : undefined
}

function normalizedIdentifier(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}
