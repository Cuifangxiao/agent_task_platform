<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { Download, FileOutput, Presentation, RotateCcw, Wifi } from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
import type { ParsedSseEvent } from '@/shared/api/sse'
import type { AgentConfig } from '@/types'

import type { PresentationApi } from '../api'
import {
  collectPagePreviews,
  findPptResource,
  isCancelledTaskEvent,
  isRunningTask,
  isTerminalTask,
  normalizeTaskEvent,
  projectTaskEvent,
  resolveResourceUrl,
  shouldAcceptTaskEvent,
  stageLabel,
  synchronizeTaskCursor,
  taskStatusLabel,
} from '../lib/taskProjection'
import { validatePresentationDescription, validateSourceFiles } from '../lib/presentationUtils'
import type { PptAgentEvent, PptCreateTaskPayload, PptTask, PreviewLightbox } from '../types'
import PagePreviewGrid from './PagePreviewGrid.vue'
import PresentationComposer from './PresentationComposer.vue'
import PresentationPreviewDialog from './PresentationPreviewDialog.vue'
import TaskEventTimeline from './TaskEventTimeline.vue'

const props = defineProps<{
  configs: AgentConfig[]
  api: PresentationApi
}>()

const composer = reactive({ text: '', files: [] as File[] })
const selectedConfigId = ref('')
const task = ref<PptTask | null>(null)
const events = ref<PptAgentEvent[]>([])
const lastEventSequence = ref(0)
const error = ref('')
const uploadState = ref('')
const connectionState = ref('')
const submitting = ref(false)
const cancelling = ref(false)
const downloading = ref(false)
const submittedDescription = ref('')
const previewLightbox = ref<PreviewLightbox | null>(null)
const previewSources = reactive<Record<string, string>>({})
const seenSequences = new Set<number>()
const pendingPreviewResources = new Set<string>()
const ownedPreviewUrls = new Set<string>()
let streamController: AbortController | null = null

watch(
  () => props.configs,
  (configs) => {
    if (configs.some((config) => String(config.id) === selectedConfigId.value)) return
    const selected = configs.find((config) => config.is_default) ?? configs[0]
    selectedConfigId.value = selected ? String(selected.id) : ''
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  streamController?.abort()
  clearPreviewSources()
})

const selectedConfig = computed(() =>
  props.configs.find((config) => String(config.id) === selectedConfigId.value) ?? null,
)
const running = computed(() => submitting.value || cancelling.value || isRunningTask(task.value))
const canSubmit = computed(() => Boolean(selectedConfig.value && composer.text.trim() && !running.value))
const previews = computed(() => collectPagePreviews(events.value))
const pptResource = computed(() => findPptResource(events.value) ?? task.value?.result?.ppt_file ?? null)
const latestEvent = computed(() => events.value[events.value.length - 1] ?? null)
const currentStageLabel = computed(() => stageLabel(latestEvent.value?.stage ?? task.value?.stage))
const statusClass = computed(() => {
  if (task.value?.status === 'succeeded') return 'is-done'
  if (task.value?.status === 'failed') return 'is-error'
  if (task.value?.status === 'cancelled') return 'is-cancelled'
  return 'is-running'
})
const progressPercent = computed(() => {
  const progress = latestEvent.value?.progress ?? task.value?.progress
  if (!progress) return 0
  if (typeof progress.percent === 'number' && Number.isFinite(progress.percent)) {
    return Math.max(0, Math.min(100, progress.percent))
  }
  return progress.total > 0 ? Math.max(0, Math.min(100, progress.current / progress.total * 100)) : 0
})

watch(previews, (items) => { void loadPreviewResources(items) })

async function startGeneration() {
  error.value = ''
  const description = composer.text.trim()
  const config = selectedConfig.value
  if (!config) {
    error.value = '请先选择一个可用的 PPTAgent 配置。'
    return
  }
  const descriptionError = validatePresentationDescription(description)
  if (descriptionError) {
    error.value = descriptionError
    return
  }
  const sourceFiles = [...composer.files]
  const fileError = validateSourceFiles(sourceFiles)
  if (fileError) {
    error.value = fileError
    return
  }

  resetStreamState()
  submitting.value = true
  uploadState.value = sourceFiles.length ? '正在上传附件并创建任务…' : '正在创建任务…'
  submittedDescription.value = description

  const payload: PptCreateTaskPayload = {
    agent_config_id: config.id,
    description,
    ...(sourceFiles.length ? { source_files: sourceFiles } : {}),
  }

  try {
    const created = await props.api.createTask(payload)
    setTaskSnapshot(created)
    composer.text = ''
    composer.files = []
    uploadState.value = sourceFiles.length ? '附件已提交，正在连接生成事件。' : '任务已创建，正在连接生成事件。'
    if (!isTerminalTask(created)) void followTask(created.task_id)
  } catch (cause) {
    error.value = errorMessage(cause)
    task.value = null
  } finally {
    submitting.value = false
    window.setTimeout(() => { uploadState.value = '' }, 1_200)
  }
}

async function followTask(taskId: string) {
  streamController?.abort()
  const controller = new AbortController()
  streamController = controller

  try {
    while (!controller.signal.aborted && task.value?.task_id === taskId) {
      connectionState.value = lastEventSequence.value > 0 ? '正在继续接收事件…' : '正在接收事件…'
      try {
        await props.api.subscribeTask(taskId, acceptEvent, controller.signal, lastEventSequence.value)
      } catch (cause) {
        if (isAbortError(cause) || controller.signal.aborted) return
        connectionState.value = '事件连接中断，正在从上次位置恢复…'
      }

      if (controller.signal.aborted) return
      const latest = await props.api.getTask(taskId).catch((cause) => {
        connectionState.value = `暂时无法读取任务状态：${errorMessage(cause)}`
        return null
      })
      if (latest && task.value?.task_id === taskId) setTaskSnapshot(latest)
      if (isTerminalTask(task.value)) {
        connectionState.value = ''
        return
      }

      connectionState.value = '事件流已结束，正在重新连接…'
      await pause(800)
    }
  } finally {
    if (streamController === controller) streamController = null
  }
}

function acceptEvent(rawEvent: ParsedSseEvent) {
  const event = normalizeTaskEvent(rawEvent)
  const currentTask = task.value
  if (!event || !currentTask || event.task_id !== currentTask.task_id) return
  if (!shouldAcceptTaskEvent(lastEventSequence.value, seenSequences, event.sequence)) return

  seenSequences.add(event.sequence)
  lastEventSequence.value = event.sequence
  events.value.push(event)
  task.value = projectTaskEvent(currentTask, event)

  if (event.type === 'TASK_FAILED') {
    error.value = isCancelledTaskEvent(event) ? '' : event.error?.message || event.message
  }
  if (event.type === 'TASK_COMPLETED' || event.type === 'TASK_FAILED') {
    connectionState.value = ''
    streamController?.abort()
  }
}

async function cancelTask() {
  if (!task.value || !isRunningTask(task.value) || cancelling.value) return
  cancelling.value = true
  error.value = ''
  try {
    setTaskSnapshot(await props.api.cancelTask(task.value.task_id))
    streamController?.abort()
    connectionState.value = ''
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    cancelling.value = false
  }
}

function setTaskSnapshot(snapshot: PptTask) {
  task.value = snapshot
  lastEventSequence.value = synchronizeTaskCursor(lastEventSequence.value, snapshot)
}

async function downloadTask() {
  if (!task.value || downloading.value) return
  downloading.value = true
  error.value = ''
  try {
    const result = await props.api.downloadTaskFile(task.value.task_id)
    const url = URL.createObjectURL(result.blob)
    const link = document.createElement('a')
    link.href = url
    link.download = result.filename || pptResource.value?.name || `presentation-${task.value.task_id}.pptx`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    downloading.value = false
  }
}

function resetTask() {
  streamController?.abort()
  task.value = null
  submittedDescription.value = ''
  resetStreamState()
}

function resetStreamState() {
  streamController?.abort()
  streamController = null
  events.value = []
  seenSequences.clear()
  lastEventSequence.value = 0
  connectionState.value = ''
  error.value = ''
  clearPreviewSources()
}

async function loadPreviewResources(items: ReturnType<typeof collectPagePreviews>) {
  const taskId = task.value?.task_id
  if (!taskId) return
  await Promise.all(items.map(async (preview) => {
    const resource = preview.resource
    const loadKey = `${taskId}:${resource.resource_id}`
    if (previewSources[resource.resource_id] || pendingPreviewResources.has(loadKey)) return
    pendingPreviewResources.add(loadKey)
    try {
      if (resource.relative_path) {
        const result = await props.api.downloadTaskFile(taskId, resource.relative_path)
        const objectUrl = URL.createObjectURL(result.blob)
        if (task.value?.task_id !== taskId) {
          URL.revokeObjectURL(objectUrl)
          return
        }
        ownedPreviewUrls.add(objectUrl)
        previewSources[resource.resource_id] = objectUrl
      } else if (resource.url && task.value?.task_id === taskId) {
        previewSources[resource.resource_id] = resolveResourceUrl(resource.url)
      }
    } catch (cause) {
      connectionState.value = `页面预览暂时无法读取：${errorMessage(cause)}`
    } finally {
      pendingPreviewResources.delete(loadKey)
    }
  }))
}

function clearPreviewSources() {
  for (const url of ownedPreviewUrls) URL.revokeObjectURL(url)
  ownedPreviewUrls.clear()
  pendingPreviewResources.clear()
  for (const resourceId of Object.keys(previewSources)) delete previewSources[resourceId]
}

function addFiles(files: File[]) {
  const nextFiles = [...composer.files, ...files]
  const fileError = validateSourceFiles(nextFiles)
  if (fileError) {
    error.value = fileError
    return
  }
  composer.files = nextFiles
  error.value = ''
}

function removeFile(index: number) {
  composer.files = composer.files.filter((_, currentIndex) => currentIndex !== index)
}

function pause(milliseconds: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds))
}

function isAbortError(cause: unknown) {
  return cause instanceof DOMException && cause.name === 'AbortError'
}

function errorMessage(cause: unknown) {
  return cause instanceof Error ? cause.message : String(cause)
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
          <span>任务状态</span>
          <strong>{{ taskStatusLabel(task?.status) }}</strong>
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
        :error="error"
        :files="composer.files"
        :running="running"
        :text="composer.text"
        :upload-state="uploadState"
        @add-files="addFiles"
        @remove-file="removeFile"
        @stop="cancelTask"
        @submit="startGeneration"
        @update-config-id="selectedConfigId = $event"
        @update-text="composer.text = $event"
      />

      <section v-if="task" class="task-overview">
        <div class="task-overview-icon"><FileOutput :size="22" /></div>
        <div class="task-overview-copy">
          <div class="status-header">
            <strong>{{ currentStageLabel }}</strong>
            <span class="status-pill" :class="statusClass">{{ taskStatusLabel(task.status) }}</span>
          </div>
          <p>{{ latestEvent?.message || task.message || submittedDescription }}</p>
          <code>{{ task.task_id }}</code>
          <div v-if="progressPercent > 0" class="task-progress" :aria-label="`生成进度 ${Math.round(progressPercent)}%`">
            <span :style="{ width: `${progressPercent}%` }" />
          </div>
          <span v-if="connectionState" class="connection-state"><Wifi :size="14" />{{ connectionState }}</span>
        </div>
        <div class="task-overview-actions">
          <button v-if="task.status === 'succeeded'" class="btn primary" type="button" :disabled="downloading" @click="downloadTask">
            <Download :size="17" />
            {{ downloading ? '准备下载…' : '下载 PPT' }}
          </button>
          <button v-if="isTerminalTask(task)" class="btn ghost" type="button" @click="resetTask">
            <RotateCcw :size="16" />
            新建任务
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
          <strong>一条不中断的生成流水线</strong>
          <p>选择配置、描述内容并上传资料。任务创建后，这里会显示实时事件和可用的页面预览。</p>
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
</style>
