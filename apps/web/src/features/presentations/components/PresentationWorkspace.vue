<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  Download,
  Loader2,
  MessageSquarePlus,
  MessagesSquare,
  Presentation,
  Square,
  Wifi,
} from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
import type { AgentConfig } from '@/types'

import type { PresentationApi } from '../api'
import {
  collectPagePreviews,
  eventBelongsToTask,
  findPptResource,
  hasAppliedTaskSnapshot,
  isCancelledTaskEvent,
  isRunningTask,
  isTerminalTask,
  overallTaskProgress,
  projectTaskEvent,
  resolveResourceUrl,
  shouldAcceptTaskEvent,
  stageLabel,
  taskSnapshotWatermark,
  taskEventIdentity,
  taskStatusLabel,
} from '../lib/taskProjection'
import {
  createPresentationSubmissionAttempt,
  formatDateTime,
  MAX_DESCRIPTION_CHARACTERS,
  validatePresentationDescription,
  validateSourceFiles,
} from '../lib/presentationUtils'
import type { PresentationSubmissionAttempt } from '../lib/presentationUtils'
import type { PptAgentEvent, PptCreateTaskPayload, PptTask, PreviewLightbox } from '../types'
import type { PresentationWebSocketClient, PresentationWebSocketConnectionState } from '../websocket'
import PagePreviewGrid from './PagePreviewGrid.vue'
import PresentationConversation from './PresentationConversation.vue'
import PresentationComposer from './PresentationComposer.vue'
import PresentationPreviewDialog from './PresentationPreviewDialog.vue'

interface RunViewState {
  task: PptTask
  events: PptAgentEvent[]
  lastEventSequence: number
  projectedEventSequence: number
  error: string
  connectionState: string
  cancelling: boolean
  downloading: boolean
  submittedDescription: string
  previewSources: Record<string, string>
}

interface RunRuntimeState {
  seenEventIds: Set<string>
  pendingPreviewResources: Set<string>
  ownedPreviewUrls: Set<string>
  subscribed: boolean
  historyHydrated: boolean
  historyHydrating: boolean
}

interface ComposerDraft {
  text: string
  files: File[]
}

const props = defineProps<{
  configs: AgentConfig[]
  api: PresentationApi
}>()

const MAX_TRACKED_TASKS = 20
const composer = reactive({ text: '', files: [] as File[] })
const selectedConfigId = ref('')
const selectedTaskId = ref('')
const taskOrder = ref<string[]>([])
const runs = reactive<Record<string, RunViewState>>({})
const runtimeByTaskId = new Map<string, RunRuntimeState>()
const composerDrafts = new Map<string, ComposerDraft>()
const NEW_CONVERSATION_DRAFT_KEY = '__new_conversation__'
let realtimeClient: PresentationWebSocketClient | null = null
let realtimeState: PresentationWebSocketConnectionState = 'closed'
let failedCreateAttempt: PresentationSubmissionAttempt | null = null
const composerError = ref('')
const uploadState = ref('')
const submitting = ref(false)
const restoring = ref(false)
const previewLightbox = ref<PreviewLightbox | null>(null)
const revisionComposerOpen = ref(false)

watch(
  () => props.configs,
  (configs) => {
    if (configs.some((config) => String(config.id) === selectedConfigId.value)) return
    const selected = configs.find((config) => config.is_default) ?? configs[0]
    selectedConfigId.value = selected ? String(selected.id) : ''
  },
  { immediate: true },
)

watch([selectedConfigId, () => composer.text, () => composer.files], () => {
  failedCreateAttempt = null
})

onMounted(() => {
  initializeRealtimeClient()
  void restoreTasks()
})

onBeforeUnmount(() => {
  realtimeClient?.close()
  realtimeClient = null
  for (const taskId of [...runtimeByTaskId.keys()]) clearRunResources(taskId)
  composerDrafts.clear()
})

const selectedConfig = computed(() =>
  props.configs.find((config) => String(config.id) === selectedConfigId.value) ?? null,
)
const trackedRuns = computed(() =>
  taskOrder.value.map((taskId) => runs[taskId]).filter((run): run is RunViewState => Boolean(run)),
)
const selectedRun = computed(() => runs[selectedTaskId.value] ?? null)
const task = computed(() => selectedRun.value?.task ?? null)
const events = computed(() => selectedRun.value?.events ?? [])
const previewSources = computed(() => selectedRun.value?.previewSources ?? {})
const canSubmit = computed(() => Boolean(selectedConfig.value && composer.text.trim() && !submitting.value))
const previews = computed(() => collectPagePreviews(events.value))
const latestEvent = computed(() => events.value[events.value.length - 1] ?? null)
const currentStageLabel = computed(() => stageLabel(latestEvent.value?.stage ?? task.value?.stage))
const selectedProgressPercent = computed(() => overallTaskProgress(task.value, events.value))
const activeTaskCount = computed(() => trackedRuns.value.filter((run) => isRunningTask(run.task)).length)
const isRevisionComposer = computed(() => Boolean(selectedRun.value))
const composerTitle = computed(() => isRevisionComposer.value ? '复制当前需求创建新会话' : '开始一个新的 PPT 会话')
const composerHelper = computed(() => isRevisionComposer.value
  ? '会复制当前文字需求并创建独立任务；原附件和已生成页面不会自动带入。'
  : '描述受众、目标、页数和视觉偏好，PPTAgent 会开始独立生成。')
const composerPlaceholder = computed(() => isRevisionComposer.value
  ? '例如：沿用当前主题，把内容压缩到 6 页，并增加一页商业模式。'
  : '例如：制作一份 AI Agent 平台 MVP 汇报，面向投资人，风格简洁专业，控制在 10 页左右。')
const composerSubmitLabel = computed(() => isRevisionComposer.value ? '复制需求并生成' : '开始生成')
const conversationExamples = [
  {
    label: '融资路演',
    prompt: '制作一份面向投资人的 AI Agent 平台融资路演，突出市场机会、产品价值、商业模式和融资计划，控制在 10 页，风格简洁专业。',
  },
  {
    label: '项目汇报',
    prompt: '制作一份项目阶段汇报，包含目标、已完成工作、关键数据、当前风险和下一步计划，控制在 8 页，适合管理层快速阅读。',
  },
  {
    label: '培训课件',
    prompt: '制作一份面向初级工程师的技术培训课件，从背景、核心概念、工作原理到实践案例逐步展开，控制在 12 页，风格清晰易懂。',
  },
]

watch(
  previews,
  (items) => {
    const run = selectedRun.value
    if (run) void loadPreviewResources(run, items)
  },
  { immediate: true },
)

watch(selectedTaskId, (taskId) => {
  const run = runs[taskId]
  if (run && isTerminalTask(run.task) && run.events.length === 0) void hydrateTaskHistory(taskId)
})

async function restoreTasks() {
  if (!props.api.listTasks || restoring.value) return
  restoring.value = true
  try {
    const snapshots = await props.api.listTasks({ limit: MAX_TRACKED_TASKS })
    const ordered = [...snapshots].sort((left, right) => timestamp(right.created_at) - timestamp(left.created_at))
    for (const snapshot of ordered) upsertTask(snapshot)
    if (!selectedTaskId.value && taskOrder.value[0]) {
      saveComposerDraft()
      selectedTaskId.value = taskOrder.value[0]
      restoreComposerDraft(selectedTaskId.value)
    }
    for (const run of trackedRuns.value) {
      if (!isTerminalTask(run.task)) void subscribeRun(run.task.task_id, 0)
    }
  } catch (cause) {
    composerError.value = `暂时无法恢复最近任务：${errorMessage(cause)}`
  } finally {
    restoring.value = false
  }
}

async function startGeneration() {
  composerError.value = ''
  const request = composer.text.trim()
  const requestError = validatePresentationDescription(request)
  if (requestError) {
    composerError.value = requestError
    return
  }
  const previousDescription = selectedRun.value
    ? runLabel(selectedRun.value)
    : ''
  const description = selectedRun.value
    ? buildCopiedConversationDescription(previousDescription, request)
    : request
  const config = selectedConfig.value
  if (!config) {
    composerError.value = '请先选择一个可用的 PPTAgent 配置。'
    return
  }
  const descriptionError = validatePresentationDescription(description)
  if (descriptionError) {
    composerError.value = descriptionError
    return
  }
  const sourceFiles = [...composer.files]
  const fileError = validateSourceFiles(sourceFiles)
  if (fileError) {
    composerError.value = fileError
    return
  }

  submitting.value = true
  uploadState.value = sourceFiles.length ? '正在上传附件并创建任务…' : '正在创建任务…'
  const createAttempt = createPresentationSubmissionAttempt({
    agentConfigId: config.id,
    description,
    sourceFiles,
  }, failedCreateAttempt)
  const payload: PptCreateTaskPayload = {
    agent_config_id: config.id,
    description,
    thread_id: createAttempt.threadId,
    client_request_id: createAttempt.clientRequestId,
    ...(sourceFiles.length ? { source_files: sourceFiles } : {}),
  }

  try {
    const submittedDraftKey = composerDraftKey()
    const created = await props.api.createTask(payload)
    const snapshot = {
      ...created,
      thread_id: created.thread_id || createAttempt.threadId,
      run_id: created.run_id || created.task_id,
    }
    failedCreateAttempt = null
    const run = upsertTask(snapshot, { prepend: true, submittedDescription: description })
    composerDrafts.delete(submittedDraftKey)
    selectedTaskId.value = run.task.task_id
    revisionComposerOpen.value = false
    previewLightbox.value = null
    composer.text = ''
    composer.files = []
    uploadState.value = sourceFiles.length
      ? '附件已提交，任务会在后台继续生成。'
      : '任务已创建，可以继续提交新的 PPT。'
    if (!isTerminalTask(run.task)) void subscribeRun(run.task.task_id, 0)
  } catch (cause) {
    failedCreateAttempt = createAttempt
    composerError.value = errorMessage(cause)
  } finally {
    submitting.value = false
    window.setTimeout(() => { uploadState.value = '' }, 1_200)
  }
}

function initializeRealtimeClient() {
  if (realtimeClient || !props.api.createRealtimeClient) return
  realtimeClient = props.api.createRealtimeClient({
    onSnapshot: acceptSnapshot,
    onEvent: acceptEvent,
    onConnectionState: updateRealtimeState,
    onSequenceGap: reportSequenceGap,
    onSubscriptionError: handleSubscriptionError,
  })
  realtimeClient.connect()
}

async function subscribeRun(taskId: string, after = 0, history = false) {
  const run = runs[taskId]
  const runtime = runtimeFor(taskId)
  if (!run || !realtimeClient || runtime.subscribed) return
  runtime.subscribed = true
  if (history) runtime.historyHydrating = true
  run.connectionState = history ? '正在读取历史事件…' : realtimeConnectionLabel(realtimeState)
  try {
    await realtimeClient.subscribe(run.task.run_id || taskId, after)
  } catch (cause) {
    if (runs[taskId]) {
      runs[taskId].connectionState = `实时订阅暂时不可用：${errorMessage(cause)}`
    }
    if (isNonRetryableSubscribeError(cause)) {
      runtime.subscribed = false
      runtime.historyHydrating = false
    }
  }
}

async function hydrateTaskHistory(taskId: string) {
  const run = runs[taskId]
  const runtime = runtimeFor(taskId)
  if (!run || runtime.historyHydrated || runtime.historyHydrating || runtime.subscribed) return
  await subscribeRun(taskId, 0, true)
}

function acceptSnapshot(snapshot: PptTask) {
  const taskId = resolveTaskId(snapshot.run_id || snapshot.task_id)
  const run = taskId ? runs[taskId] : null
  if (!run) return
  setTaskSnapshot(taskId, snapshot)
  if (isTerminalTask(snapshot)) {
    const runtime = runtimeFor(taskId)
    const watermark = taskSnapshotWatermark(snapshot)
    if (hasAppliedTaskSnapshot(run.lastEventSequence, snapshot)) {
      runtime.subscribed = false
      runtime.historyHydrating = false
      runtime.historyHydrated = true
      run.connectionState = ''
    } else {
      runtime.subscribed = true
      runtime.historyHydrated = false
      run.connectionState = watermark === null
        ? '任务已结束，正在同步终态事件…'
        : `任务已结束，正在同步终态事件 ${run.lastEventSequence}/${watermark}…`
    }
  }
}

function acceptEvent(event: PptAgentEvent) {
  const taskId = resolveTaskId(event.run_id || event.task_id)
  const run = taskId ? runs[taskId] : null
  if (!taskId || !run || !eventBelongsToTask(event, run.task)) return

  const runtime = runtimeFor(taskId)
  if (!shouldAcceptTaskEvent(run.lastEventSequence, runtime.seenEventIds, event)) return

  runtime.seenEventIds.add(taskEventIdentity(event))
  run.lastEventSequence = Math.max(run.lastEventSequence, event.sequence)
  run.events.push(event)
  if (event.sequence > run.projectedEventSequence) {
    run.task = projectTaskEvent(run.task, event)
    run.projectedEventSequence = event.sequence
  }
  if (event.type === 'TASK_FAILED') {
    run.error = isCancelledTaskEvent(event) ? '' : event.error?.message || event.message
  }
  if (event.type === 'TASK_COMPLETED' || event.type === 'TASK_FAILED') {
    run.connectionState = ''
    runtime.subscribed = false
    runtime.historyHydrating = false
    runtime.historyHydrated = true
  }
}

function updateRealtimeState(state: PresentationWebSocketConnectionState, cause?: Error) {
  realtimeState = state
  for (const [taskId, runtime] of runtimeByTaskId) {
    const run = runs[taskId]
    if (!run || !runtime.subscribed) continue
    run.connectionState = cause?.message
      ? `${realtimeConnectionLabel(state)}：${cause.message}`
      : realtimeConnectionLabel(state)
  }
}

function reportSequenceGap(runId: string, expected: number, received: number) {
  const taskId = resolveTaskId(runId)
  const run = taskId ? runs[taskId] : null
  if (run) run.connectionState = `检测到事件缺口 ${expected}–${received - 1}，正在补齐…`
}

function handleSubscriptionError(runId: string, cause: Error) {
  const taskId = resolveTaskId(runId)
  const run = taskId ? runs[taskId] : null
  if (!taskId || !run) return
  const runtime = runtimeFor(taskId)
  runtime.subscribed = false
  runtime.historyHydrating = false
  run.connectionState = `实时订阅失败：${cause.message}`
}

async function cancelTask(taskId = selectedTaskId.value) {
  const run = runs[taskId]
  if (!run || !isRunningTask(run.task) || run.cancelling) return
  run.cancelling = true
  run.error = ''
  try {
    const runId = run.task.run_id || taskId
    if (realtimeClient && realtimeState === 'open') {
      await realtimeClient.cancel(runId)
      run.connectionState = '取消请求已发送，等待任务结束…'
    } else {
      setTaskSnapshot(taskId, await props.api.cancelTask(taskId))
    }
  } catch (cause) {
    try {
      setTaskSnapshot(taskId, await props.api.cancelTask(taskId))
    } catch (fallbackCause) {
      run.error = errorMessage(fallbackCause || cause)
    }
  } finally {
    run.cancelling = false
  }
}

async function downloadTask(taskId = selectedTaskId.value) {
  const run = runs[taskId]
  if (!run || run.downloading) return
  run.downloading = true
  run.error = ''
  try {
    const result = await props.api.downloadTaskFile(taskId)
    const resource = findPptResource(run.events) ?? run.task.result?.ppt_file
    const url = URL.createObjectURL(result.blob)
    const link = document.createElement('a')
    link.href = url
    link.download = result.filename || resource?.name || `presentation-${taskId}.pptx`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (cause) {
    run.error = errorMessage(cause)
  } finally {
    run.downloading = false
  }
}

function selectTask(taskId: string) {
  if (!runs[taskId] || selectedTaskId.value === taskId) return
  saveComposerDraft()
  selectedTaskId.value = taskId
  revisionComposerOpen.value = false
  restoreComposerDraft(taskId)
  previewLightbox.value = null
  const runtime = runtimeFor(taskId)
  if (isTerminalTask(runs[taskId]?.task) && runs[taskId]?.events.length === 0 && !runtime.historyHydrated) {
    void hydrateTaskHistory(taskId)
  }
}

function startNewConversation() {
  if (selectedTaskId.value) saveComposerDraft()
  failedCreateAttempt = null
  selectedTaskId.value = ''
  revisionComposerOpen.value = false
  previewLightbox.value = null
  restoreComposerDraft(NEW_CONVERSATION_DRAFT_KEY)
  void focusComposer()
}

function openRevisionComposer() {
  revisionComposerOpen.value = true
  void focusComposer()
}

function composerDraftKey(taskId = selectedTaskId.value) {
  return taskId || NEW_CONVERSATION_DRAFT_KEY
}

function saveComposerDraft(taskId = selectedTaskId.value) {
  const key = composerDraftKey(taskId)
  if (!composer.text.trim() && composer.files.length === 0) {
    composerDrafts.delete(key)
    return
  }
  composerDrafts.set(key, {
    text: composer.text,
    files: [...composer.files],
  })
}

function restoreComposerDraft(taskId: string) {
  const draft = composerDrafts.get(composerDraftKey(taskId))
  composer.text = draft?.text ?? ''
  composer.files = draft ? [...draft.files] : []
  failedCreateAttempt = null
  composerError.value = ''
  uploadState.value = ''
}

async function focusComposer() {
  await nextTick()
  document.querySelector<HTMLTextAreaElement>('.composer-dock textarea')?.focus()
}

function buildCopiedConversationDescription(previousDescription: string, request: string) {
  const separator = '\n\n本次新会话补充要求：\n'
  const remainingCharacters = MAX_DESCRIPTION_CHARACTERS
    - Array.from(separator).length
    - Array.from(request).length
  const previousContext = compactDescription(previousDescription, Math.max(0, remainingCharacters))
  return previousContext ? `${previousContext}${separator}${request}` : request
}

function compactDescription(value: string, maximumCharacters: number) {
  const characters = Array.from(value)
  if (characters.length <= maximumCharacters) return value
  if (maximumCharacters <= 0) return ''
  const omission = Array.from('\n…（较早的文字需求已压缩）…\n')
  if (maximumCharacters <= omission.length) return characters.slice(-maximumCharacters).join('')
  const available = maximumCharacters - omission.length
  const headLength = Math.ceil(available * 0.58)
  const tailLength = available - headLength
  return [
    ...characters.slice(0, headLength),
    ...omission,
    ...characters.slice(characters.length - tailLength),
  ].join('')
}

function openConversationPreview(preview: ReturnType<typeof collectPagePreviews>[number]) {
  const src = previewSources.value[preview.resource.resource_id]
    || (preview.resource.url ? resolveResourceUrl(preview.resource.url) : '')
  if (!src) return
  previewLightbox.value = {
    src,
    title: preview.page?.title || preview.resource.name || '页面预览',
    subtitle: preview.page?.page_index ? `第 ${preview.page.page_index} 页` : '页面预览',
  }
}

function upsertTask(
  snapshot: PptTask,
  options: { prepend?: boolean; submittedDescription?: string } = {},
) {
  const taskId = snapshot.task_id
  const current = runs[taskId]
  if (current) {
    current.task = { ...current.task, ...snapshot }
    current.projectedEventSequence = Math.max(
      current.projectedEventSequence,
      taskSnapshotWatermark(snapshot) ?? 0,
    )
    if (options.submittedDescription) current.submittedDescription = options.submittedDescription
    synchronizeRunError(current)
    return current
  }

  const run = reactive<RunViewState>({
    task: snapshot,
    events: [],
    lastEventSequence: 0,
    projectedEventSequence: taskSnapshotWatermark(snapshot) ?? 0,
    error: '',
    connectionState: '',
    cancelling: false,
    downloading: false,
    submittedDescription: options.submittedDescription ?? '',
    previewSources: {},
  })
  runs[taskId] = run
  runtimeFor(taskId)
  taskOrder.value = options.prepend
    ? [taskId, ...taskOrder.value.filter((id) => id !== taskId)]
    : [...taskOrder.value.filter((id) => id !== taskId), taskId]
  synchronizeRunError(run)
  trimTaskWindow()
  return run
}

function setTaskSnapshot(taskId: string, snapshot: PptTask) {
  const run = runs[taskId]
  if (!run) return
  run.task = {
    ...snapshot,
    thread_id: snapshot.thread_id || run.task.thread_id,
    run_id: snapshot.run_id || run.task.run_id,
  }
  run.projectedEventSequence = Math.max(run.projectedEventSequence, taskSnapshotWatermark(snapshot) ?? 0)
  synchronizeRunError(run)
}

function synchronizeRunError(run: RunViewState) {
  if (run.task.status === 'failed') run.error = run.task.error?.message || run.task.message || 'PPT 生成失败'
  if (run.task.status === 'cancelled' || run.task.status === 'succeeded') run.error = ''
}

function runtimeFor(taskId: string) {
  let runtime = runtimeByTaskId.get(taskId)
  if (!runtime) {
    runtime = {
      seenEventIds: new Set<string>(),
      pendingPreviewResources: new Set<string>(),
      ownedPreviewUrls: new Set<string>(),
      subscribed: false,
      historyHydrated: false,
      historyHydrating: false,
    }
    runtimeByTaskId.set(taskId, runtime)
  }
  return runtime
}

async function loadPreviewResources(run: RunViewState, items: ReturnType<typeof collectPagePreviews>) {
  const taskId = run.task.task_id
  const runtime = runtimeFor(taskId)
  await Promise.all(items.map(async (preview) => {
    const resource = preview.resource
    const loadKey = `${taskId}:${resource.resource_id}`
    if (run.previewSources[resource.resource_id] || runtime.pendingPreviewResources.has(loadKey)) return
    runtime.pendingPreviewResources.add(loadKey)
    try {
      if (resource.relative_path) {
        const result = await props.api.downloadTaskFile(taskId, resource.relative_path)
        const objectUrl = URL.createObjectURL(result.blob)
        if (runs[taskId] !== run) {
          URL.revokeObjectURL(objectUrl)
          return
        }
        runtime.ownedPreviewUrls.add(objectUrl)
        run.previewSources[resource.resource_id] = objectUrl
      } else if (resource.url && runs[taskId] === run) {
        run.previewSources[resource.resource_id] = resolveResourceUrl(resource.url)
      }
    } catch (cause) {
      run.connectionState = `页面预览暂时无法读取：${errorMessage(cause)}`
    } finally {
      runtime.pendingPreviewResources.delete(loadKey)
    }
  }))
}

function trimTaskWindow() {
  while (taskOrder.value.length > MAX_TRACKED_TASKS) {
    let removableIndex = -1
    for (let index = taskOrder.value.length - 1; index >= 0; index -= 1) {
      const taskId = taskOrder.value[index]
      if (taskId && taskId !== selectedTaskId.value && isTerminalTask(runs[taskId]?.task)) {
        removableIndex = index
        break
      }
    }
    if (removableIndex < 0) return
    const [taskId] = taskOrder.value.splice(removableIndex, 1)
    if (!taskId) return
    clearRunResources(taskId)
    delete runs[taskId]
  }
}

function clearRunResources(taskId: string) {
  const runtime = runtimeByTaskId.get(taskId)
  const runId = runs[taskId]?.task.run_id || taskId
  if (runtime?.subscribed) void realtimeClient?.unsubscribe(runId).catch(() => {})
  for (const url of runtime?.ownedPreviewUrls ?? []) URL.revokeObjectURL(url)
  runtime?.ownedPreviewUrls.clear()
  runtime?.pendingPreviewResources.clear()
  runtimeByTaskId.delete(taskId)
  composerDrafts.delete(taskId)
}

function addFiles(files: File[]) {
  const nextFiles = [...composer.files, ...files]
  const fileError = validateSourceFiles(nextFiles)
  if (fileError) {
    composerError.value = fileError
    return
  }
  composer.files = nextFiles
  composerError.value = ''
}

function removeFile(index: number) {
  composer.files = composer.files.filter((_, currentIndex) => currentIndex !== index)
}

function useConversationExample(prompt: string) {
  if (submitting.value) return
  composer.text = prompt
  composerError.value = ''
}

function statusClass(taskSnapshot: PptTask | null | undefined) {
  if (taskSnapshot?.status === 'succeeded') return 'is-done'
  if (taskSnapshot?.status === 'failed') return 'is-error'
  if (taskSnapshot?.status === 'cancelled') return 'is-cancelled'
  return 'is-running'
}

function runProgress(run: RunViewState | null | undefined) {
  return run ? overallTaskProgress(run.task, run.events) : 0
}

function runLabel(run: RunViewState) {
  return run.task.description
    || run.submittedDescription
    || run.task.message
    || `PPT 任务 ${shortId(run.task.run_id || run.task.task_id)}`
}

function shortId(value: string | undefined) {
  if (!value) return ''
  return value.length > 12 ? `${value.slice(0, 8)}…` : value
}

function timestamp(value: string | undefined) {
  const parsed = value ? Date.parse(value) : 0
  return Number.isFinite(parsed) ? parsed : 0
}

function resolveTaskId(runOrTaskId: string | undefined) {
  if (!runOrTaskId) return ''
  if (runs[runOrTaskId]) return runOrTaskId
  return taskOrder.value.find((taskId) => runs[taskId]?.task.run_id === runOrTaskId) ?? ''
}

function realtimeConnectionLabel(state: PresentationWebSocketConnectionState) {
  switch (state) {
    case 'open':
      return '实时连接已就绪'
    case 'connecting':
      return '正在建立实时连接…'
    case 'reconnecting':
      return '实时连接中断，正在恢复…'
    default:
      return '实时连接已断开，任务仍在后台运行'
  }
}

function errorMessage(cause: unknown) {
  return cause instanceof Error ? cause.message : String(cause)
}

function isNonRetryableSubscribeError(cause: unknown) {
  if (!(cause instanceof Error)) return false
  const commandError = cause as Error & { command?: string; retryable?: boolean }
  return commandError.command === 'run.subscribe' && commandError.retryable === false
}
</script>

<template>
  <section class="page management-page presentation-page">
    <header class="page-head management-head presentation-page-head">
      <div class="page-title-block">
        <span class="page-eyebrow">智能演示文稿</span>
        <h2>PPT 对话工作台</h2>
        <p>每份 PPT 都会保留为独立会话；你可以并行生成，并随时回来查看消息、页面预览和最终文件。</p>
      </div>
      <div v-if="props.configs.length > 0" class="presentation-meta" aria-label="工作台概览">
        <div class="page-meta-card">
          <span>当前配置</span>
          <strong>{{ selectedConfig?.name || '未选择' }}</strong>
        </div>
        <div class="page-meta-card">
          <span>会话状态</span>
          <strong>{{ activeTaskCount }} 进行中 · {{ trackedRuns.length }} 个最近会话</strong>
        </div>
      </div>
    </header>

    <EmptyState
      v-if="props.configs.length === 0"
      :icon="Presentation"
      title="还没有可用的 PPT 配置"
      text="请先到 Agent 管理里创建一个启用的 PPTAgent 配置，再回来发起生成。"
    />

    <div
      v-else
      class="conversation-workspace"
      :class="{ 'has-preview': task }"
      aria-label="PPT 对话生成工作区"
    >
      <aside class="task-rail conversation-sidebar" aria-labelledby="tracked-ppt-runs">
        <div class="task-rail-head conversation-sidebar-head">
          <div>
            <span class="section-kicker">CONVERSATIONS</span>
            <h3 id="tracked-ppt-runs">PPT 会话</h3>
          </div>
          <span class="task-rail-status">
            <Loader2 v-if="restoring" class="spin" :size="13" />
            {{ restoring ? '恢复中' : `${trackedRuns.length} 个会话` }}
          </span>
        </div>

        <button
          class="new-conversation-button"
          :class="{ selected: !selectedTaskId }"
          type="button"
          :aria-pressed="!selectedTaskId"
          @click="startNewConversation"
        >
          <MessageSquarePlus :size="17" />
          <span>新建 PPT 会话</span>
        </button>

        <div v-if="trackedRuns.length" class="task-rail-list conversation-list">
          <button
            v-for="run in trackedRuns"
            :key="run.task.task_id"
            class="task-rail-item"
            :class="{ selected: run.task.task_id === selectedTaskId }"
            type="button"
            :aria-pressed="run.task.task_id === selectedTaskId"
            @click="selectTask(run.task.task_id)"
          >
            <div class="task-rail-title">
              <span class="task-status-dot" :class="statusClass(run.task)" />
              <strong>{{ runLabel(run) }}</strong>
            </div>
            <div class="task-rail-stage">
              <span>{{ stageLabel(run.task.stage) }}</span>
              <span>{{ runProgress(run) }}%</span>
            </div>
            <div
              class="task-rail-progress"
              role="progressbar"
              :aria-label="`${runLabel(run)} 进度 ${runProgress(run)}%`"
              :aria-valuenow="runProgress(run)"
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <span :style="{ width: `${runProgress(run)}%` }" />
            </div>
            <div class="task-rail-meta">
              <span class="status-pill" :class="statusClass(run.task)">{{ taskStatusLabel(run.task.status) }}</span>
              <time v-if="run.task.created_at">{{ formatDateTime(run.task.created_at) }}</time>
            </div>
          </button>
        </div>

        <div v-else class="conversation-list-empty">
          <MessagesSquare :size="22" />
          <p>创建后，每份 PPT 都会作为一个独立会话保留在这里。</p>
        </div>
      </aside>

      <section class="conversation-main" :class="{ 'is-empty': !task }" aria-label="当前 PPT 会话">
        <div v-if="task" class="conversation-toolbar" aria-live="polite">
          <div class="conversation-toolbar-copy">
            <span>{{ currentStageLabel }}</span>
            <h3>{{ selectedRun ? runLabel(selectedRun) : 'PPT 会话' }}</h3>
            <p>
              <code>run {{ shortId(task.run_id || task.task_id) }}</code>
              <span v-if="task.created_at">创建于 {{ formatDateTime(task.created_at) }}</span>
              <span v-if="task.result?.slide_count">{{ task.result.slide_count }} 页</span>
            </p>
          </div>
          <div class="conversation-toolbar-actions">
            <button
              class="btn ghost"
              type="button"
              :aria-expanded="revisionComposerOpen"
              @click="openRevisionComposer"
            >
              <MessageSquarePlus :size="16" />
              创建新版本
            </button>
            <button
              v-if="isRunningTask(task)"
              class="btn ghost"
              type="button"
              :disabled="selectedRun?.cancelling"
              @click="cancelTask(task.task_id)"
            >
              <Loader2 v-if="selectedRun?.cancelling" class="spin" :size="16" />
              <Square v-else :size="16" />
              {{ selectedRun?.cancelling ? '正在取消…' : '取消任务' }}
            </button>
            <button
              v-if="task.status === 'succeeded'"
              class="btn primary"
              type="button"
              :disabled="selectedRun?.downloading"
              @click="downloadTask(task.task_id)"
            >
              <Loader2 v-if="selectedRun?.downloading" class="spin" :size="17" />
              <Download v-else :size="17" />
              {{ selectedRun?.downloading ? '准备下载…' : '下载 PPT' }}
            </button>
          </div>
        </div>

        <PresentationConversation
          v-if="task && selectedRun"
          :events="events"
          :previews="previews"
          :sources="previewSources"
          :submitted-description="selectedRun.submittedDescription"
          :task="task"
          @open-preview="openConversationPreview"
        />

        <section v-else class="conversation-welcome">
          <span class="conversation-welcome-icon"><Presentation :size="30" /></span>
          <span class="page-eyebrow">NEW CONVERSATION</span>
          <h3>{{ restoring ? '正在恢复最近会话' : '从一个清晰的演示目标开始' }}</h3>
          <p>描述受众、内容、页数和视觉风格。任务创建后可以离开页面，PPTAgent 会在后台继续生成。</p>
          <div class="conversation-examples" aria-label="需求示例">
            <button
              v-for="example in conversationExamples"
              :key="example.label"
              type="button"
              :disabled="submitting"
              @click="useConversationExample(example.prompt)"
            >
              {{ example.label }}
            </button>
          </div>
        </section>

        <div v-if="!task || revisionComposerOpen" class="composer-dock">
          <PresentationComposer
            mode="conversation"
            :can-submit="canSubmit"
            :config-id="selectedConfigId"
            :configs="props.configs"
            :error="composerError"
            :files="composer.files"
            :helper="composerHelper"
            :placeholder="composerPlaceholder"
            :submit-label="composerSubmitLabel"
            :submitting="submitting"
            :text="composer.text"
            :title="composerTitle"
            :upload-state="uploadState"
            @add-files="addFiles"
            @remove-file="removeFile"
            @submit="startGeneration"
            @update-config-id="selectedConfigId = $event"
            @update-text="composer.text = $event"
          />
        </div>
      </section>

      <aside v-if="task" class="preview-rail" aria-label="当前会话页面预览">
        <div v-if="task" class="preview-progress-card">
          <div class="preview-progress-head">
            <div>
              <span class="section-kicker">CURRENT RUN</span>
              <strong>{{ currentStageLabel }}</strong>
            </div>
            <span class="status-pill" :class="statusClass(task)">{{ taskStatusLabel(task.status) }}</span>
          </div>
          <div class="task-progress-row">
            <div
              class="task-progress"
              role="progressbar"
              :aria-label="`总体生成进度 ${selectedProgressPercent}%`"
              :aria-valuenow="selectedProgressPercent"
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <span :style="{ width: `${selectedProgressPercent}%` }" />
            </div>
            <strong>{{ selectedProgressPercent }}%</strong>
          </div>
          <p>{{ latestEvent?.message || task.message || selectedRun?.submittedDescription }}</p>
          <span v-if="selectedRun?.connectionState" class="connection-state">
            <Wifi :size="14" />{{ selectedRun.connectionState }}
          </span>
          <p v-if="selectedRun?.error" class="run-error">{{ selectedRun.error }}</p>
        </div>

        <PagePreviewGrid
          v-if="task"
          compact
          :previews="previews"
          :sources="previewSources"
          @open="previewLightbox = $event"
        />

      </aside>
    </div>

    <PresentationPreviewDialog :preview="previewLightbox" @close="previewLightbox = null" />
  </section>
</template>

<style scoped>
.presentation-page {
  min-width: 0;
  min-height: 0;
  height: 100%;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 14px;
  padding-bottom: 0;
  overflow: hidden;
}

.presentation-page-head {
  align-items: center;
  flex-wrap: wrap;
  min-width: 0;
}

.presentation-page-head .page-title-block {
  flex: 1 1 520px;
  min-width: 0;
}

.presentation-page-head .page-title-block h2 {
  font-size: 25px;
}

.presentation-page-head .page-title-block p {
  max-width: 720px;
  line-height: 1.55;
}

.presentation-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: stretch;
}

.presentation-meta .page-meta-card {
  min-width: 168px;
  max-width: 250px;
  border-radius: 8px;
  background: var(--panel);
  box-shadow: none;
}

.presentation-meta .page-meta-card:last-child {
  min-width: 224px;
}

.section-kicker {
  color: var(--accent-strong);
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.12em;
}

.task-workspace {
  display: grid;
  gap: 20px;
  min-width: 0;
}

.run-workbench {
  min-width: 0;
  display: grid;
  grid-template-columns: 276px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.task-rail {
  position: sticky;
  top: 18px;
  min-width: 0;
  max-height: calc(100vh - 36px);
  overflow: hidden;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}

.task-rail-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: flex-start;
  border-bottom: 1px solid var(--line);
  padding: 15px 16px 13px;
}

.task-rail-head h3 {
  margin-top: 3px;
  color: var(--text);
  font-size: 15px;
}

.task-rail-status {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  color: var(--muted);
  font-size: 10px;
  white-space: nowrap;
}

.task-rail-list {
  min-height: 0;
  overflow-y: auto;
  display: grid;
  align-content: start;
  gap: 8px;
  padding: 10px;
}

.task-rail-item {
  min-width: 0;
  display: grid;
  gap: 8px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: var(--panel-soft);
  color: inherit;
  padding: 12px;
  text-align: left;
  transition: border-color 140ms ease, background 140ms ease, box-shadow 140ms ease;
}

.task-rail-item:hover {
  border-color: var(--line-strong);
  background: #fff;
}

.task-rail-item.selected {
  border-color: rgba(18, 124, 112, 0.45);
  background: var(--accent-soft);
  box-shadow: inset 3px 0 0 var(--accent);
}

.task-rail-item:focus-visible {
  outline: 3px solid rgba(18, 124, 112, 0.18);
  outline-offset: 1px;
}

.task-rail-title,
.task-rail-stage,
.task-rail-meta {
  min-width: 0;
  display: flex;
  align-items: center;
}

.task-rail-title { gap: 7px; }

.task-rail-title strong {
  min-width: 0;
  flex: 1 1 auto;
  display: -webkit-box;
  overflow: hidden;
  color: var(--text);
  font-size: 12px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.task-status-dot {
  width: 7px;
  height: 7px;
  flex: 0 0 auto;
  border-radius: 999px;
  background: var(--muted-2);
}

.task-status-dot.is-running { background: var(--accent); }
.task-status-dot.is-done { background: var(--success); }
.task-status-dot.is-error { background: var(--danger); }

.task-rail-title .status-pill {
  flex: 0 0 auto;
  padding: 3px 6px;
  font-size: 9px;
}

.task-rail-stage,
.task-rail-meta {
  justify-content: space-between;
  gap: 8px;
  color: var(--muted);
  font-size: 10px;
}

.task-rail-meta {
  color: var(--muted-2);
  font-size: 9px;
}

.task-rail-meta code,
.task-rail-meta time {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-rail-progress {
  height: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(100, 116, 139, 0.12);
}

.task-rail-progress span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
}

.run-detail {
  min-width: 0;
  display: grid;
  gap: 16px;
}

.task-overview {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 14px;
  align-items: start;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 18px;
}

.task-overview-icon {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent-strong);
}

.task-overview-copy {
  min-width: 0;
  display: grid;
  gap: 8px;
}

.task-overview-copy p {
  color: var(--muted);
  font-size: 13px;
}

.task-overview-copy code {
  overflow: hidden;
  color: var(--muted-2);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-overview-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  color: var(--muted-2);
  font-size: 10px;
}

.task-overview-meta code {
  color: inherit;
}

.task-overview-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.task-progress-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.task-progress-row > strong {
  color: var(--accent-strong);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.task-progress {
  height: 6px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--panel-soft);
}

.task-progress span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transition: width 180ms ease;
}

.connection-state {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  color: var(--muted);
  font-size: 12px;
}

.run-error {
  color: var(--danger, #c54f4f) !important;
}

.task-content-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(310px, 0.8fr);
  gap: 16px;
  align-items: start;
  min-width: 0;
}

.task-intro {
  min-height: 180px;
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 14px;
  border: 1px dashed var(--line);
  border-radius: 8px;
  background: var(--panel-soft);
  color: var(--accent-strong);
  padding: 24px;
}

.task-intro strong {
  color: var(--text);
}

.task-intro p {
  max-width: 640px;
  margin-top: 4px;
  color: var(--muted);
}

.conversation-workspace {
  min-width: 0;
  min-height: 0;
  height: 100%;
  display: grid;
  grid-template-columns: 230px minmax(520px, 1fr) minmax(280px, 310px);
  gap: 14px;
  align-items: stretch;
}

.conversation-workspace:not(.has-preview) {
  grid-template-columns: 230px minmax(0, 1fr);
}

.conversation-sidebar {
  position: static;
  top: auto;
  max-height: none;
  grid-template-rows: auto auto minmax(0, 1fr);
}

.conversation-sidebar-head {
  border-bottom: 0;
  padding-bottom: 9px;
}

.new-conversation-button {
  min-width: 0;
  min-height: 42px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid rgba(18, 124, 112, 0.2);
  border-radius: 7px;
  background: var(--accent-soft);
  color: var(--accent-strong);
  margin: 0 10px 8px;
  padding: 9px 11px;
  font-size: 12px;
  font-weight: 700;
  text-align: left;
}

.new-conversation-button:hover,
.new-conversation-button.selected {
  border-color: var(--accent);
  background: #fff;
}

.new-conversation-button:focus-visible {
  outline: 3px solid rgba(18, 124, 112, 0.18);
  outline-offset: 1px;
}

.conversation-list {
  border-top: 1px solid var(--line);
  padding-top: 10px;
}

.conversation-list .task-rail-item {
  gap: 7px;
}

.conversation-list .task-rail-meta .status-pill {
  flex: 0 0 auto;
  padding: 3px 6px;
  font-size: 9px;
}

.conversation-list-empty {
  min-height: 0;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  border-top: 1px solid var(--line);
  color: var(--muted-2);
  padding: 20px;
  text-align: center;
}

.conversation-list-empty p {
  max-width: 170px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.6;
}

.conversation-main {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f6f8f8;
  padding: 12px;
}

.conversation-main.is-empty {
  grid-template-rows: auto auto;
  align-content: center;
  padding: 18px;
}

.conversation-main > .presentation-conversation {
  min-height: 0;
}

.conversation-toolbar {
  min-width: 0;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 12px 14px;
}

.conversation-toolbar-copy {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.conversation-toolbar-copy > span {
  color: var(--accent-strong);
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.08em;
}

.conversation-toolbar-copy h3 {
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conversation-toolbar-copy p {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  color: var(--muted-2);
  font-size: 9px;
}

.conversation-toolbar-copy code {
  color: inherit;
}

.conversation-toolbar-actions {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
}

.conversation-toolbar-actions .btn {
  min-height: 36px;
  padding: 7px 10px;
  font-size: 11px;
}

.conversation-welcome {
  min-width: 0;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 9px;
  border: 1px dashed var(--line-strong);
  border-radius: 8px;
  background: var(--panel);
  padding: 24px 28px;
  text-align: center;
}

.conversation-welcome-icon {
  width: 46px;
  height: 46px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: var(--accent-soft);
  color: var(--accent-strong);
}

.conversation-welcome h3 {
  color: var(--text);
  font-size: 19px;
}

.conversation-welcome p {
  max-width: 520px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.65;
}

.conversation-examples {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 7px;
  margin-top: 4px;
}

.conversation-examples button {
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--panel-soft);
  color: var(--muted);
  padding: 5px 9px;
  font-size: 10px;
}

.conversation-examples button:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-strong);
}

.conversation-examples button:focus-visible {
  outline: 3px solid rgba(18, 124, 112, 0.18);
  outline-offset: 2px;
}

.composer-dock {
  min-width: 0;
}

.conversation-main.is-empty .composer-dock {
  width: min(760px, 100%);
  margin-inline: auto;
}

.preview-rail {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  display: grid;
  align-content: start;
  gap: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 14px;
  scrollbar-gutter: stable;
}

.preview-progress-card {
  display: grid;
  gap: 9px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 13px;
}

.preview-progress-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: flex-start;
}

.preview-progress-head > div {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.preview-progress-head strong {
  overflow: hidden;
  color: var(--text);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-progress-card > p {
  overflow-wrap: anywhere;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.55;
}

.preview-progress-card .connection-state {
  font-size: 10px;
}

.preview-conversation-empty {
  min-height: 280px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  border: 1px dashed var(--line);
  border-radius: 8px;
  color: var(--muted-2);
  padding: 22px;
  text-align: center;
}

.preview-conversation-empty strong {
  color: var(--text);
  font-size: 13px;
}

.preview-conversation-empty p {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.6;
}

@media (min-width: 1101px) and (min-height: 760px) {
  .conversation-workspace {
    height: 100%;
    max-height: none;
  }
}

@media (max-width: 1480px) and (min-width: 1181px) {
  .conversation-workspace.has-preview {
    grid-template-columns: 230px minmax(0, 1fr);
  }

  .preview-rail {
    display: none;
  }
}

@media (max-width: 1180px) {
  .run-workbench {
    grid-template-columns: 1fr;
  }

  .task-rail {
    position: static;
    max-height: none;
  }

  .task-rail-list {
    overflow: visible;
    grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr));
  }

  .conversation-workspace {
    min-height: 0;
    height: auto;
    max-height: none;
    grid-template-columns: 210px minmax(0, 1fr);
  }

  .conversation-sidebar {
    position: static;
    max-height: none;
  }

  .conversation-list {
    overflow-y: auto;
    grid-template-columns: 1fr;
  }

  .preview-rail {
    grid-column: 2;
    max-height: none;
    overflow: visible;
  }
}

@media (max-width: 920px) {
  .task-content-grid {
    grid-template-columns: 1fr;
  }

  .task-overview {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .task-overview-actions {
    grid-column: 1 / -1;
    justify-content: flex-start;
  }
}

@media (max-width: 760px) {
  .presentation-page-head,
  .presentation-meta {
    align-items: stretch;
    flex-direction: column;
  }

  .presentation-page-head .page-title-block {
    flex: 0 1 auto;
    width: 100%;
  }

  .presentation-page-head .page-title-block p {
    overflow-wrap: anywhere;
  }

  .presentation-meta .page-meta-card,
  .presentation-meta .page-meta-card:last-child {
    width: 100%;
    max-width: none;
    min-width: 0;
  }

  .task-overview {
    grid-template-columns: minmax(0, 1fr);
  }

  .task-overview-icon {
    display: none;
  }

  .task-overview-actions {
    grid-column: auto;
  }

  .conversation-workspace {
    grid-template-columns: minmax(0, 1fr);
  }

  .conversation-sidebar,
  .conversation-main,
  .preview-rail {
    grid-column: 1;
  }

  .conversation-sidebar {
    grid-template-rows: auto auto auto;
  }

  .conversation-list {
    display: flex;
    overflow-x: auto;
    overflow-y: hidden;
    padding-bottom: 12px;
    scrollbar-width: thin;
  }

  .conversation-list .task-rail-item {
    width: 230px;
    flex: 0 0 230px;
  }

  .conversation-main {
    min-height: 720px;
    overflow: visible;
  }

  .conversation-main > .presentation-conversation {
    min-height: 440px;
  }

  .conversation-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .conversation-toolbar-actions {
    justify-content: flex-start;
  }

  .preview-rail {
    overflow: visible;
  }
}

@media (max-width: 520px) {
  .task-rail-head { align-items: center; }
  .task-overview-actions .btn { width: 100%; }
  .task-progress-row { grid-template-columns: minmax(0, 1fr) auto; }
  .conversation-toolbar-actions .btn { flex: 1 1 auto; }
  .conversation-welcome { padding: 26px 18px; }
}

@media (prefers-reduced-motion: reduce) {
  .task-rail-item,
  .task-progress span {
    transition: none;
  }
}
</style>
