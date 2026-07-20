<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Download, FileOutput, Loader2, Presentation, Square, Wifi } from '@lucide/vue'

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
  validatePresentationDescription,
  validateSourceFiles,
} from '../lib/presentationUtils'
import type { PresentationSubmissionAttempt } from '../lib/presentationUtils'
import type { PptAgentEvent, PptCreateTaskPayload, PptTask, PreviewLightbox } from '../types'
import type { PresentationWebSocketClient, PresentationWebSocketConnectionState } from '../websocket'
import PagePreviewGrid from './PagePreviewGrid.vue'
import PresentationComposer from './PresentationComposer.vue'
import PresentationPreviewDialog from './PresentationPreviewDialog.vue'
import TaskEventTimeline from './TaskEventTimeline.vue'

interface RunViewState {
  task: PptTask
  events: PptAgentEvent[]
  lastEventSequence: number
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
let realtimeClient: PresentationWebSocketClient | null = null
let realtimeState: PresentationWebSocketConnectionState = 'closed'
let failedCreateAttempt: PresentationSubmissionAttempt | null = null
const composerError = ref('')
const uploadState = ref('')
const submitting = ref(false)
const restoring = ref(false)
const previewLightbox = ref<PreviewLightbox | null>(null)

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
const selectedProgressPercent = computed(() => progressPercent(selectedRun.value))
const activeTaskCount = computed(() => trackedRuns.value.filter((run) => isRunningTask(run.task)).length)

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
    if (!selectedTaskId.value && taskOrder.value[0]) selectedTaskId.value = taskOrder.value[0]
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
  const description = composer.text.trim()
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
    const created = await props.api.createTask(payload)
    const snapshot = {
      ...created,
      thread_id: created.thread_id || createAttempt.threadId,
      run_id: created.run_id || created.task_id,
    }
    failedCreateAttempt = null
    const run = upsertTask(snapshot, { prepend: true, submittedDescription: description })
    selectedTaskId.value = run.task.task_id
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
  run.task = projectTaskEvent(run.task, event)
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
  if (!runs[taskId]) return
  selectedTaskId.value = taskId
  previewLightbox.value = null
  const runtime = runtimeFor(taskId)
  if (isTerminalTask(runs[taskId]?.task) && runs[taskId]?.events.length === 0 && !runtime.historyHydrated) {
    void hydrateTaskHistory(taskId)
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
    if (options.submittedDescription) current.submittedDescription = options.submittedDescription
    synchronizeRunError(current)
    return current
  }

  const run = reactive<RunViewState>({
    task: snapshot,
    events: [],
    lastEventSequence: 0,
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

function statusClass(taskSnapshot: PptTask | null | undefined) {
  if (taskSnapshot?.status === 'succeeded') return 'is-done'
  if (taskSnapshot?.status === 'failed') return 'is-error'
  if (taskSnapshot?.status === 'cancelled') return 'is-cancelled'
  return 'is-running'
}

function progressPercent(run: RunViewState | null | undefined) {
  const latest = run?.events[run.events.length - 1]
  const progress = latest?.progress ?? run?.task.progress
  if (!progress) return 0
  if (typeof progress.percent === 'number' && Number.isFinite(progress.percent)) {
    return Math.max(0, Math.min(100, progress.percent))
  }
  return progress.total > 0 ? Math.max(0, Math.min(100, progress.current / progress.total * 100)) : 0
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
  <section class="page chat-page management-page">
    <header class="page-head management-head chat-page-head">
      <div class="page-title-block chat-page-copy">
        <span class="page-eyebrow">智能演示文稿</span>
        <h2>PPT 生成任务</h2>
        <p>提交一次需求，Python Agent 会持续发送事件，直到本地 PPT 文件生成完成。</p>
      </div>
      <div v-if="props.configs.length > 0" class="chat-page-meta">
        <div class="page-meta-card">
          <span>当前配置</span>
          <strong>{{ selectedConfig?.name || '未选择' }}</strong>
        </div>
        <div class="page-meta-card">
          <span>活动任务</span>
          <strong>{{ activeTaskCount }} / {{ trackedRuns.length }}</strong>
        </div>
      </div>
    </header>

    <EmptyState
      v-if="props.configs.length === 0"
      :icon="Presentation"
      title="还没有可用的 PPT 配置"
      text="请先到 Agent 管理里创建一个 active 的 PPTAgent 配置，再回来发起生成。"
    />

    <div v-else class="task-workspace">
      <PresentationComposer
        :can-submit="canSubmit"
        :config-id="selectedConfigId"
        :configs="props.configs"
        :error="composerError"
        :files="composer.files"
        :submitting="submitting"
        :text="composer.text"
        :upload-state="uploadState"
        @add-files="addFiles"
        @remove-file="removeFile"
        @submit="startGeneration"
        @update-config-id="selectedConfigId = $event"
        @update-text="composer.text = $event"
      />

      <section v-if="trackedRuns.length" class="task-switcher" aria-labelledby="tracked-ppt-runs">
        <div class="panel-head task-switcher-head">
          <div>
            <span class="chat-shell-kicker">Concurrent runs</span>
            <h3 id="tracked-ppt-runs">最近任务</h3>
          </div>
          <span class="chat-note">
            <Loader2 v-if="restoring" class="spin" :size="13" />
            {{ restoring ? '正在恢复' : `${activeTaskCount} 个生成中` }}
          </span>
        </div>

        <div class="task-switcher-list">
          <article
            v-for="run in trackedRuns"
            :key="run.task.task_id"
            class="task-switch-card"
            :class="{ selected: run.task.task_id === selectedTaskId }"
          >
            <button class="task-switch-select" type="button" @click="selectTask(run.task.task_id)">
              <div class="task-switch-title">
                <strong>{{ stageLabel(run.task.stage) }}</strong>
                <span class="status-pill" :class="statusClass(run.task)">{{ taskStatusLabel(run.task.status) }}</span>
              </div>
              <p>{{ runLabel(run) }}</p>
              <div class="task-switch-meta">
                <code>{{ shortId(run.task.run_id || run.task.task_id) }}</code>
                <time v-if="run.task.created_at">{{ formatDateTime(run.task.created_at) }}</time>
              </div>
              <span v-if="run.connectionState" class="connection-state">
                <Wifi :size="13" />{{ run.connectionState }}
              </span>
            </button>
            <div class="task-switch-actions">
              <button
                v-if="isRunningTask(run.task)"
                class="btn ghost compact"
                type="button"
                :disabled="run.cancelling"
                @click="cancelTask(run.task.task_id)"
              >
                <Loader2 v-if="run.cancelling" class="spin" :size="14" />
                <Square v-else :size="14" />
                {{ run.cancelling ? '取消中' : '取消' }}
              </button>
              <button
                v-if="run.task.status === 'succeeded'"
                class="btn ghost compact"
                type="button"
                :disabled="run.downloading"
                @click="downloadTask(run.task.task_id)"
              >
                <Loader2 v-if="run.downloading" class="spin" :size="14" />
                <Download v-else :size="14" />
                {{ run.downloading ? '准备中' : '下载' }}
              </button>
            </div>
          </article>
        </div>
      </section>

      <section v-if="task" class="task-overview">
        <div class="task-overview-icon"><FileOutput :size="22" /></div>
        <div class="task-overview-copy">
          <div class="status-header">
            <strong>{{ currentStageLabel }}</strong>
            <span class="status-pill" :class="statusClass(task)">{{ taskStatusLabel(task.status) }}</span>
          </div>
          <p>{{ latestEvent?.message || task.message || selectedRun?.submittedDescription }}</p>
          <code>run {{ task.run_id || task.task_id }} · thread {{ task.thread_id || 'legacy' }}</code>
          <div
            v-if="selectedProgressPercent > 0"
            class="task-progress"
            :aria-label="`生成进度 ${Math.round(selectedProgressPercent)}%`"
          >
            <span :style="{ width: `${selectedProgressPercent}%` }" />
          </div>
          <span v-if="selectedRun?.connectionState" class="connection-state">
            <Wifi :size="14" />{{ selectedRun.connectionState }}
          </span>
          <p v-if="selectedRun?.error" class="run-error">{{ selectedRun.error }}</p>
        </div>
        <div class="task-overview-actions">
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
            <Download :size="17" />
            {{ selectedRun?.downloading ? '准备下载…' : '下载 PPT' }}
          </button>
        </div>
      </section>

      <div v-if="task" class="task-content-grid">
        <TaskEventTimeline :events="events" />
        <PagePreviewGrid :previews="previews" :sources="previewSources" @open="previewLightbox = $event" />
      </div>

      <section v-else class="task-intro">
        <Presentation :size="30" />
        <div>
          <strong>{{ restoring ? '正在恢复最近任务' : '可并行的生成流水线' }}</strong>
          <p>每次提交会创建独立会话。已有任务会继续在后台生成，你可以随时切换回来查看事件与页面预览。</p>
        </div>
      </section>
    </div>

    <PresentationPreviewDialog :preview="previewLightbox" @close="previewLightbox = null" />
  </section>
</template>

<style scoped>
.task-workspace {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.task-switcher {
  display: grid;
  gap: 12px;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 16px 18px;
}

.task-switcher-head {
  padding: 0;
}

.task-switcher-head h3 {
  margin-top: 4px;
  color: var(--text);
  font-size: 16px;
}

.task-switcher-head .chat-note {
  display: inline-flex;
  gap: 6px;
  align-items: center;
}

.task-switcher-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(280px, 100%), 1fr));
  gap: 10px;
}

.task-switch-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: stretch;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel-soft);
  transition: border-color 160ms ease, background 160ms ease;
}

.task-switch-card.selected {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.task-switch-select {
  min-width: 0;
  display: grid;
  gap: 6px;
  border: 0;
  background: transparent;
  color: inherit;
  padding: 12px;
  text-align: left;
  cursor: pointer;
}

.task-switch-title,
.task-switch-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.task-switch-title strong {
  overflow: hidden;
  color: var(--text);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-switch-select p {
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-switch-meta {
  color: var(--muted-2);
  font-size: 10px;
}

.task-switch-meta code,
.task-switch-meta time {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-switch-actions {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
  border-left: 1px solid var(--line);
  padding: 8px;
}

.btn.compact {
  min-height: 30px;
  padding: 5px 8px;
  font-size: 11px;
}

.task-overview {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 16px 18px;
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
  gap: 6px;
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

.task-overview-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
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
  grid-template-columns: minmax(0, 1.45fr) minmax(300px, 0.75fr);
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

@media (max-width: 900px) {
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

@media (max-width: 560px) {
  .task-switch-card {
    grid-template-columns: 1fr;
  }

  .task-switch-actions {
    flex-direction: row;
    justify-content: flex-start;
    border-top: 1px solid var(--line);
    border-left: 0;
  }
}
</style>
