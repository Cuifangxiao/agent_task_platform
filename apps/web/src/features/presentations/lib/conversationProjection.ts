import type {
  PptAgentEvent,
  PptFinalResult,
  PptPageDetail,
  PptProgress,
  PptResource,
  PptTask,
  PptTaskStatus,
} from '../types'
import {
  PRESENTATION_PHASES,
  eventBelongsToTask,
  phaseIdForStage,
  projectTaskPhases,
  taskEventIdentity,
  type PresentationPhaseId,
  type PresentationPhaseStatus,
} from './taskProjection.ts'

export type PresentationConversationRole = 'user' | 'assistant'
export type PresentationConversationMessageKind =
  | 'request'
  | 'milestone'
  | 'page_progress'
  | 'completion'
  | 'error'
  | 'cancelled'

export interface PresentationConversationMessage {
  /** Stable across event replays and progress updates; safe to use as a Vue key. */
  key: string
  conversationId: string
  role: PresentationConversationRole
  kind: PresentationConversationMessageKind
  title: string
  content: string
  phase?: PresentationPhaseId
  phaseStatus?: PresentationPhaseStatus
  sequence: number
  updatedSequence: number
  createdAt?: string
  updatedAt?: string
  progress?: PptProgress
  page?: PptPageDetail
  resources?: PptResource[]
  finalResult?: PptFinalResult
}

export interface PresentationConversation {
  id: string
  taskId: string
  runId?: string
  threadId?: string
  title: string
  status: PptTaskStatus
  messages: PresentationConversationMessage[]
  updatedAt?: string
}

const PHASE_COPY: Record<Exclude<PresentationPhaseId, 'done'>, {
  active: string
  done: string
}> = {
  prepare: {
    active: '正在读取需求与附件，准备生成所需资料。',
    done: '需求与附件已经准备完成。',
  },
  plan: {
    active: '正在梳理演示文稿的大纲与叙事结构。',
    done: '演示文稿的大纲与叙事结构已经确定。',
  },
  design: {
    active: '正在确定配色、字体与页面版式。',
    done: '演示文稿的视觉风格与版式已经确定。',
  },
  generate: {
    active: '正在按照大纲逐页生成演示文稿。',
    done: '全部页面已经生成。',
  },
  export: {
    active: '正在组装并校验 PPT 文件。',
    done: 'PPT 文件已经导出。',
  },
}

/**
 * Projects one presentation run into a user-facing conversation.
 *
 * Raw transport events stay available elsewhere for diagnostics. This view is
 * deliberately sparse: one initial user request, one message per reached phase,
 * one replace-in-place page progress message, and one terminal message.
 */
export function projectPresentationConversation(
  task: PptTask,
  events: readonly PptAgentEvent[],
): PresentationConversation {
  const conversationId = presentationConversationId(task)
  const orderedEvents = conversationEvents(task, events)
  const phaseViews = projectTaskPhases(orderedEvents, task)
  const messages: PresentationConversationMessage[] = []

  messages.push({
    key: `${conversationId}:request`,
    conversationId,
    role: 'user',
    kind: 'request',
    title: '生成需求',
    content: normalizedText(task.description) || '请根据已提交的需求生成一份演示文稿。',
    sequence: 0,
    updatedSequence: 0,
    ...(task.created_at ? { createdAt: task.created_at, updatedAt: task.created_at } : {}),
  })

  for (const phase of phaseViews) {
    if (phase.id === 'done' || phase.status === 'pending') continue
    const phaseEvents = orderedEvents.filter((event) => phaseIdForStage(event.stage) === phase.id)
    const firstEvent = phaseEvents[0]
    const lastEvent = phaseEvents[phaseEvents.length - 1]
    const status = phase.status === 'done' ? 'done' : 'active'
    const copy = PHASE_COPY[phase.id]

    messages.push({
      key: `${conversationId}:phase:${phase.id}`,
      conversationId,
      role: 'assistant',
      kind: 'milestone',
      phase: phase.id,
      phaseStatus: phase.status,
      title: phase.label,
      content: copy[status],
      sequence: firstEvent?.sequence ?? inferredPhaseSequence(phase.id),
      updatedSequence: lastEvent?.sequence ?? inferredPhaseSequence(phase.id),
      ...(firstEvent?.created_at || task.created_at
        ? { createdAt: firstEvent?.created_at || task.created_at }
        : {}),
      ...(lastEvent?.created_at || task.updated_at
        ? { updatedAt: lastEvent?.created_at || task.updated_at }
        : {}),
    })
  }

  const pageMessage = projectPageProgressMessage(conversationId, orderedEvents, task)
  if (pageMessage) messages.push(pageMessage)

  const terminalMessage = projectTerminalMessage(conversationId, orderedEvents, task)
  if (terminalMessage) messages.push(terminalMessage)

  messages.sort(compareConversationMessages)

  return {
    id: conversationId,
    taskId: task.task_id,
    ...(task.run_id ? { runId: task.run_id } : {}),
    ...(task.thread_id ? { threadId: task.thread_id } : {}),
    title: conversationTitle(task.name || task.description),
    status: task.status,
    messages,
    ...(task.updated_at || task.finished_at || orderedEvents.at(-1)?.created_at
      ? { updatedAt: task.updated_at || task.finished_at || orderedEvents.at(-1)?.created_at }
      : {}),
  }
}

export function presentationConversationId(task: Pick<PptTask, 'task_id' | 'run_id'>) {
  return task.run_id?.trim() || task.task_id.trim()
}

function conversationEvents(task: PptTask, events: readonly PptAgentEvent[]) {
  const unique = new Map<string, PptAgentEvent>()
  for (const event of events) {
    if (!eventBelongsToTask(event, task)) continue
    const identity = taskEventIdentity(event)
    const existing = unique.get(identity)
    if (!existing || event.sequence >= existing.sequence) unique.set(identity, event)
  }
  return [...unique.values()].sort((left, right) => left.sequence - right.sequence)
}

function projectPageProgressMessage(
  conversationId: string,
  events: readonly PptAgentEvent[],
  task: PptTask,
): PresentationConversationMessage | null {
  const pageEvents = events.filter(isPageEvent)
  const first = pageEvents[0]
  const latest = pageEvents.at(-1)
  const snapshotIsPageProgress = phaseIdForStage(task.stage) === 'generate'
    && Boolean(task.progress?.total)
  if (!latest && !snapshotIsPageProgress) return null

  const pageSnapshot = snapshotIsPageProgress ? task.progress : undefined
  const current = maximumProgressValue(pageEvents, pageSnapshot, 'current')
  const total = maximumProgressValue(pageEvents, pageSnapshot, 'total')
  const latestPage = latest?.page
  const latestPreviewEvent = [...pageEvents].reverse().find((event) => (
    event.resources?.some((resource) => resource.type === 'PAGE_PREVIEW')
  ))
  const previews = latestPreviewEvent
    ? deduplicatedResources(latestPreviewEvent.resources?.filter((resource) => resource.type === 'PAGE_PREVIEW') ?? [])
    : []
  const previewReady = previews.length > 0
  const pageNumber = Math.max(current, latestPage?.page_index ?? 0)
  const pageSuffix = total > 0
    ? `第 ${pageNumber || 1}/${total} 页`
    : pageNumber > 0 ? `第 ${pageNumber} 页` : '页面'
  const pageTitle = normalizedText(latestPage?.title)
  const titleSuffix = pageTitle ? `「${pageTitle}」` : ''
  const progress = projectedPageProgress(pageEvents, pageSnapshot, current, total)

  return {
    key: `${conversationId}:page-progress`,
    conversationId,
    role: 'assistant',
    kind: 'page_progress',
    phase: 'generate',
    phaseStatus: task.status === 'failed' ? 'error'
      : task.status === 'cancelled' ? 'cancelled'
        : task.status === 'succeeded' || (total > 0 && current >= total) ? 'done' : 'active',
    title: previewReady ? '页面预览已更新' : '页面生成进度',
    content: previewReady && latestPreviewEvent?.sequence === latest?.sequence
      ? `${pageSuffix}${titleSuffix}的预览已经就绪。`
      : previewReady
        ? `正在生成${pageSuffix}${titleSuffix}，最新页面预览已经就绪。`
        : `正在生成${pageSuffix}${titleSuffix}。`,
    sequence: first?.sequence ?? inferredPhaseSequence('generate') + 1,
    updatedSequence: latest?.sequence ?? task.last_event_sequence ?? inferredPhaseSequence('generate') + 1,
    ...(first?.created_at || task.created_at ? { createdAt: first?.created_at || task.created_at } : {}),
    ...(latest?.created_at || task.updated_at ? { updatedAt: latest?.created_at || task.updated_at } : {}),
    ...(progress ? { progress } : {}),
    ...(latestPage ? { page: latestPage } : {}),
    ...(previews.length ? { resources: previews } : {}),
  }
}

function projectTerminalMessage(
  conversationId: string,
  events: readonly PptAgentEvent[],
  task: PptTask,
): PresentationConversationMessage | null {
  if (task.status === 'running') return null
  const terminalEvent = [...events].reverse().find((event) => (
    event.type === 'TASK_COMPLETED' || event.type === 'TASK_FAILED'
  ))
  const sequence = terminalEvent?.sequence ?? task.last_event_sequence ?? inferredPhaseSequence('done')
  const timestamp = terminalEvent?.created_at || task.finished_at || task.updated_at

  if (task.status === 'failed') {
    const content = normalizedText(task.error?.message)
      || normalizedText(terminalEvent?.error?.message)
      || '生成过程中出现错误，请检查需求后重试。'
    return terminalMessage({
      conversationId,
      kind: 'error',
      title: '生成失败',
      content,
      status: 'error',
      sequence,
      timestamp,
    })
  }

  if (task.status === 'cancelled') {
    return terminalMessage({
      conversationId,
      kind: 'cancelled',
      title: '任务已取消',
      content: '这次生成已经取消，你可以调整需求后重新开始。',
      status: 'cancelled',
      sequence,
      timestamp,
    })
  }

  const finalResult = task.result || terminalEvent?.final_result
  const resources = completionResources(finalResult, terminalEvent?.resources)
  const slideCount = finalResult?.slide_count
  return terminalMessage({
    conversationId,
    kind: 'completion',
    title: 'PPT 生成完成',
    content: slideCount && slideCount > 0
      ? `演示文稿已经生成完成，共 ${slideCount} 页，可以预览或下载。`
      : '演示文稿已经生成完成，可以预览或下载。',
    status: 'done',
    sequence,
    timestamp,
    ...(resources.length ? { resources } : {}),
    ...(finalResult ? { finalResult } : {}),
  })
}

function terminalMessage(options: {
  conversationId: string
  kind: 'completion' | 'error' | 'cancelled'
  title: string
  content: string
  status: PresentationPhaseStatus
  sequence: number
  timestamp?: string
  resources?: PptResource[]
  finalResult?: PptFinalResult
}): PresentationConversationMessage {
  return {
    key: `${options.conversationId}:terminal`,
    conversationId: options.conversationId,
    role: 'assistant',
    kind: options.kind,
    phase: 'done',
    phaseStatus: options.status,
    title: options.title,
    content: options.content,
    sequence: options.sequence,
    updatedSequence: options.sequence,
    ...(options.timestamp ? { createdAt: options.timestamp, updatedAt: options.timestamp } : {}),
    ...(options.resources ? { resources: options.resources } : {}),
    ...(options.finalResult ? { finalResult: options.finalResult } : {}),
  }
}

function isPageEvent(event: PptAgentEvent) {
  if (phaseIdForStage(event.stage) !== 'generate') return false
  return Boolean(
    event.page
    || event.progress?.unit === 'page'
    || event.resources?.some((resource) => resource.type === 'PAGE_PREVIEW'),
  )
}

function projectedPageProgress(
  events: readonly PptAgentEvent[],
  snapshot: PptProgress | undefined,
  current: number,
  total: number,
): PptProgress | undefined {
  const latest = events.at(-1)?.progress
  if (!latest && !snapshot && total <= 0) return undefined
  const percent = total > 0
    ? Math.min(100, Math.round(current / total * 100))
    : latest?.percent ?? snapshot?.percent
  return {
    current,
    total,
    ...(typeof percent === 'number' ? { percent } : {}),
    unit: 'page',
  }
}

function maximumProgressValue(
  events: readonly PptAgentEvent[],
  snapshot: PptProgress | undefined,
  field: 'current' | 'total',
) {
  if (field === 'current') {
    const completed = events
      .filter((event) => event.type === 'ARTIFACT_READY')
      .map((event) => Number(event.progress?.current) || 0)
    return Math.max(0, completed.length ? 0 : Number(snapshot?.current) || 0, ...completed)
  }
  return Math.max(
    0,
    Number(snapshot?.total) || 0,
    ...events.map((event) => Math.max(
      Number(event.progress?.total) || 0,
      Number(event.page?.page_count) || 0,
    )),
  )
}

function completionResources(result: PptFinalResult | undefined, resources: PptResource[] | undefined) {
  return deduplicatedResources([
    ...(resources ?? []),
    ...(result?.ppt_file ? [result.ppt_file] : []),
  ].filter((resource) => resource.type === 'PPTX'))
}

function deduplicatedResources(resources: readonly PptResource[]) {
  return [...new Map(resources.map((resource) => [resource.resource_id, resource])).values()]
}

function inferredPhaseSequence(phaseId: PresentationPhaseId) {
  const index = PRESENTATION_PHASES.findIndex((phase) => phase.id === phaseId)
  return -(PRESENTATION_PHASES.length - Math.max(0, index))
}

function compareConversationMessages(
  left: PresentationConversationMessage,
  right: PresentationConversationMessage,
) {
  if (left.kind === 'request') return right.kind === 'request' ? 0 : -1
  if (right.kind === 'request') return 1
  if (left.kind === 'completion' || left.kind === 'error' || left.kind === 'cancelled') {
    return right.kind === 'completion' || right.kind === 'error' || right.kind === 'cancelled' ? 0 : 1
  }
  if (right.kind === 'completion' || right.kind === 'error' || right.kind === 'cancelled') return -1

  const leftPhase = PRESENTATION_PHASES.findIndex((phase) => phase.id === left.phase)
  const rightPhase = PRESENTATION_PHASES.findIndex((phase) => phase.id === right.phase)
  if (leftPhase !== rightPhase) return leftPhase - rightPhase
  if (left.kind === 'milestone' && right.kind !== 'milestone') return -1
  if (right.kind === 'milestone' && left.kind !== 'milestone') return 1
  return left.sequence - right.sequence || left.key.localeCompare(right.key)
}

function conversationTitle(description: string | undefined) {
  const text = normalizedText(description) || '新建演示文稿'
  return text.length > 30 ? `${text.slice(0, 30)}…` : text
}

function normalizedText(value: string | undefined) {
  return value?.trim().replace(/\s+/g, ' ') || ''
}
