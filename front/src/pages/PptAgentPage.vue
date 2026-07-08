<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import {
  AlertCircle,
  CheckCircle2,
  Download,
  FileClock,
  Loader2,
  MessageSquarePlus,
  Paperclip,
  Presentation,
  Square,
  X,
} from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
import type {
  AgentConfig,
  ParsedSseEvent,
  PptConversationDetail,
  PptConversationSummary,
  PptGeneratePayload,
  PptResumePayload,
  PptStreamChunk,
} from '@/types'

const props = defineProps<{
  configs: AgentConfig[]
  listConversations: () => Promise<PptConversationSummary[]>
  loadConversation: (id: number) => Promise<PptConversationDetail>
  downloadArtifact?: (requestId: string) => Promise<{ blob: Blob; filename: string }>
  streamPpt: (payload: PptGeneratePayload, onEvent: (event: ParsedSseEvent) => void, signal: AbortSignal) => Promise<void>
  resumePpt: (payload: PptResumePayload, onEvent: (event: ParsedSseEvent) => void, signal: AbortSignal) => Promise<void>
}>()

interface ProgressStep {
  id: number
  label: string
  message: string
  tone: 'normal' | 'error' | 'done'
}

interface UserMessage {
  id: number
  role: 'user'
  kind: 'user'
  text: string
  fileName?: string
}

interface StatusMessage {
  id: number
  role: 'assistant'
  kind: 'status'
  title: string
  text: string
  requestId?: string
  state: 'running' | 'waiting' | 'done' | 'error'
  steps: ProgressStep[]
}

interface OutlineMessage {
  id: number
  role: 'assistant'
  kind: 'outline'
  title: string
  text: string
  requestId: string
  outlineMarkdown: string
  awaitingConfirm: boolean
  showEditor: boolean
}

interface ResultMessage {
  id: number
  role: 'assistant'
  kind: 'result'
  title: string
  text: string
  requestId: string
  fileName: string
}

type ChatMessage = UserMessage | StatusMessage | OutlineMessage | ResultMessage

const composer = reactive({
  text: '',
  file: null as File | null,
})

const abortController = ref<AbortController | null>(null)
const conversationList = ref<PptConversationSummary[]>([])
const currentConversationId = ref<number | null>(null)
const currentStatusMessageId = ref<number | null>(null)
const error = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const historyLoading = ref(false)
const messages = ref<ChatMessage[]>([])
const running = ref(false)
const selectedConfigId = ref('')
const threadRef = ref<HTMLElement | null>(null)

let messageSeed = 0
let stepSeed = 0

const defaultConfig = computed(() => props.configs.find((item) => item.is_default) ?? props.configs[0] ?? null)
const selectedConfig = computed(() => props.configs.find((item) => String(item.id) === selectedConfigId.value) ?? null)
const canSubmit = computed(() => Boolean(selectedConfig.value) && composer.text.trim().length > 0 && !running.value)
const currentConversationSummary = computed(() =>
  conversationList.value.find((item) => item.id === currentConversationId.value) ?? null,
)
const selectedConfigHint = computed(() => {
  if (!selectedConfig.value) {
    return '请选择一个 PPT 配置'
  }
  return selectedConfig.value.is_default ? `${selectedConfig.value.name} · 默认配置` : selectedConfig.value.name
})
const conversationCountLabel = computed(() =>
  conversationList.value.length > 0 ? `${conversationList.value.length} 条历史对话` : '暂无历史对话',
)

onMounted(() => {
  syncSelectedConfig()
  void refreshHistory({ selectLatest: true })
})

watch(
  () => props.configs,
  () => syncSelectedConfig(),
  { immediate: true },
)

function syncSelectedConfig() {
  const stillExists = props.configs.some((item) => String(item.id) === selectedConfigId.value)
  if (!stillExists) {
    selectedConfigId.value = defaultConfig.value ? String(defaultConfig.value.id) : ''
  }
}

function nextMessageId() {
  messageSeed += 1
  return messageSeed
}

function nextStepId() {
  stepSeed += 1
  return stepSeed
}

function resetDraftConversation(keepComposer = true) {
  currentConversationId.value = null
  currentStatusMessageId.value = null
  messages.value = []
  messageSeed = 0
  stepSeed = 0
  error.value = ''
  if (!keepComposer) {
    composer.text = ''
    clearSelectedFile()
  }
  syncSelectedConfig()
}

function startNewConversation() {
  resetDraftConversation(false)
}

async function refreshHistory(options: { selectLatest?: boolean; selectConversationId?: number } = {}) {
  historyLoading.value = true
  try {
    const list = await props.listConversations()
    conversationList.value = list

    if (options.selectConversationId) {
      await openConversation(options.selectConversationId)
      return
    }

    if (currentConversationId.value) {
      const stillExists = list.some((item) => item.id === currentConversationId.value)
      if (stillExists) {
        return
      }
      currentConversationId.value = null
    }

    if (options.selectLatest && list[0]) {
      await openConversation(list[0].id)
    }
  } finally {
    historyLoading.value = false
  }
}

async function openConversation(id: number) {
  if (running.value) {
    return
  }

  const detail = await props.loadConversation(id)
  currentConversationId.value = detail.id
  selectedConfigId.value = String(detail.agent_config_id)
  messages.value = detail.messages.map(normalizeStoredMessage)
  messageSeed = maxMessageId(messages.value)
  stepSeed = maxStepId(messages.value)
  currentStatusMessageId.value = null
  running.value = false
  error.value = ''
  clearSelectedFile()
  await scrollThreadToBottom(false)
}

function normalizeStoredMessage(item: Record<string, unknown>): ChatMessage {
  const id = Number(item.id ?? nextMessageId())
  const role = item.role === 'user' ? 'user' : 'assistant'
  const kind = String(item.kind ?? 'status')

  if (kind === 'user') {
    return {
      id,
      role: 'user',
      kind: 'user',
      text: String(item.text ?? ''),
      fileName: typeof item.fileName === 'string' ? item.fileName : undefined,
    }
  }

  if (kind === 'outline') {
    return {
      id,
      role: 'assistant',
      kind: 'outline',
      title: String(item.title ?? 'PPT 大纲'),
      text: String(item.text ?? ''),
      requestId: String(item.requestId ?? ''),
      outlineMarkdown: String(item.outlineMarkdown ?? ''),
      awaitingConfirm: Boolean(item.awaitingConfirm),
      showEditor: Boolean(item.showEditor),
    }
  }

  if (kind === 'result') {
    return {
      id,
      role: 'assistant',
      kind: 'result',
      title: String(item.title ?? 'PPT 已准备好'),
      text: String(item.text ?? ''),
      requestId: String(item.requestId ?? ''),
      fileName: String(item.fileName ?? ''),
    }
  }

  return {
    id,
    role: 'assistant',
    kind: 'status',
    title: String(item.title ?? '处理中'),
    text: String(item.text ?? ''),
    requestId: typeof item.requestId === 'string' ? item.requestId : undefined,
    state: normalizeStatusState(item.state),
    steps: normalizeStoredSteps(item.steps),
  }
}

function normalizeStatusState(value: unknown): StatusMessage['state'] {
  switch (value) {
    case 'running':
    case 'waiting':
    case 'done':
    case 'error':
      return value
    default:
      return 'waiting'
  }
}

function normalizeStoredSteps(value: unknown): ProgressStep[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value.map((item, index) => {
    const record = item && typeof item === 'object' ? (item as Record<string, unknown>) : {}
    return {
      id: Number(record.id ?? index + 1),
      label: String(record.label ?? '处理中'),
      message: String(record.message ?? ''),
      tone: normalizeStepTone(record.tone),
    }
  })
}

function normalizeStepTone(value: unknown): ProgressStep['tone'] {
  return value === 'error' || value === 'done' ? value : 'normal'
}

function submitPrompt() {
  void startGeneration()
}

async function startGeneration() {
  error.value = ''
  const description = composer.text.trim()
  const config = selectedConfig.value

  if (!config) {
    error.value = '请先选择一个可用的 PPTAgent 配置。'
    return
  }
  if (!description) {
    error.value = '先描述一下你想生成的 PPT 内容。'
    return
  }

  if (currentConversationId.value || messages.value.length > 0) {
    resetDraftConversation(true)
  }

  pushMessage({
    id: nextMessageId(),
    role: 'user',
    kind: 'user',
    text: description,
    fileName: composer.file?.name,
  })

  currentStatusMessageId.value = createStatusMessage(
    '正在规划大纲',
    '我先根据你的描述整理一版 PPT 大纲，稍后发给你确认。',
  )

  const payload: PptGeneratePayload = {
    agent_config_id: config.id,
    description,
    source_file: composer.file,
  }

  composer.text = ''
  clearSelectedFile()
  running.value = true
  const controller = new AbortController()
  abortController.value = controller

  try {
    await props.streamPpt(payload, handleEvent, controller.signal)
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      markCurrentRunAsStopped('已停止当前生成。')
    } else {
      const message = err instanceof Error ? err.message : String(err)
      error.value = message
      markCurrentRunAsFailed(message)
    }
  } finally {
    running.value = false
    abortController.value = null
    await syncCurrentConversationFromServer()
  }
}

function pushMessage(message: ChatMessage) {
  messages.value = [...messages.value, message]
  void scrollThreadToBottom()
}

function createStatusMessage(title: string, text: string) {
  const id = nextMessageId()
  pushMessage({
    id,
    role: 'assistant',
    kind: 'status',
    title,
    text,
    state: 'running',
    steps: [],
  })
  return id
}

function updateStatusMessage(id: number, updater: (message: StatusMessage) => void) {
  const index = messages.value.findIndex((item) => item.id === id && item.kind === 'status')
  if (index < 0) {
    return
  }
  const message = messages.value[index]
  if (message.kind !== 'status') {
    return
  }
  updater(message)
  messages.value = [...messages.value]
  void scrollThreadToBottom()
}

function updateOutlineMessage(id: number, updater: (message: OutlineMessage) => void) {
  const index = messages.value.findIndex((item) => item.id === id && item.kind === 'outline')
  if (index < 0) {
    return
  }
  const message = messages.value[index]
  if (message.kind !== 'outline') {
    return
  }
  updater(message)
  messages.value = [...messages.value]
  void scrollThreadToBottom()
}

function markCurrentRunAsStopped(message: string) {
  const statusId = currentStatusMessageId.value
  if (!statusId) {
    return
  }
  updateStatusMessage(statusId, (status) => {
    status.state = 'error'
    status.title = '已停止'
    status.text = message
    status.steps.push({
      id: nextStepId(),
      label: '已停止',
      message,
      tone: 'error',
    })
  })
}

function markCurrentRunAsFailed(message: string) {
  const statusId = currentStatusMessageId.value
  if (!statusId) {
    return
  }
  updateStatusMessage(statusId, (status) => {
    status.state = 'error'
    status.title = '生成失败'
    status.text = message
    status.steps.push({
      id: nextStepId(),
      label: '错误',
      message,
      tone: 'error',
    })
  })
}

async function confirmOutline(message: OutlineMessage) {
  error.value = ''

  const outlineMarkdown = message.outlineMarkdown.trim()
  try {
    if (!outlineMarkdown) {
      throw new Error('empty outline')
    }
  } catch {
    error.value = '请先补充大纲内容。'
    updateOutlineMessage(message.id, (draft) => {
      draft.showEditor = true
    })
    return
  }

  updateOutlineMessage(message.id, (draft) => {
    draft.awaitingConfirm = false
  })

  pushMessage({
    id: nextMessageId(),
    role: 'user',
    kind: 'user',
    text: '好的，按这个大纲继续生成完整 PPT。',
  })

  currentStatusMessageId.value = createStatusMessage(
    '继续生成 PPT',
    '我正在根据确认后的大纲展开每一页内容。',
  )
  running.value = true

  const controller = new AbortController()
  abortController.value = controller

  try {
    await props.resumePpt(
      {
        request_id: message.requestId,
        outline_markdown: outlineMarkdown,
      },
      handleEvent,
      controller.signal,
    )
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      markCurrentRunAsStopped('已停止当前生成。')
    } else {
      const failure = err instanceof Error ? err.message : String(err)
      error.value = failure
      markCurrentRunAsFailed(failure)
    }
  } finally {
    running.value = false
    abortController.value = null
    await syncCurrentConversationFromServer()
  }
}

function stop() {
  abortController.value?.abort()
}

function onSourceFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  composer.file = input.files?.[0] ?? null
  error.value = ''
}

function clearSelectedFile() {
  composer.file = null
  if (fileInput.value) {
    fileInput.value.value = ''
  }
}

function handleEvent(event: ParsedSseEvent) {
  const data = normalizeChunk(event.data)
  const statusId = currentStatusMessageId.value
  if (!statusId) {
    return
  }

  if (typeof data.conversation_id === 'number' && data.conversation_id > 0) {
    currentConversationId.value = data.conversation_id
  }

  if (data.event_type === 'text_delta' && data.channel === 'outline_markdown') {
    upsertOutlineDelta(data)
    return
  }

  updateStatusMessage(statusId, (status) => {
    if (data.request_id) {
      status.requestId = data.request_id
    }
    status.steps.push({
      id: nextStepId(),
      label: stageLabel(data.stage || data.event_type || 'progress'),
      message: data.message || defaultStepMessage(data),
      tone: data.error ? 'error' : data.done ? 'done' : 'normal',
    })
    const snapshot = describeChunk(data)
    if (snapshot) {
      status.title = snapshot.title
      status.text = snapshot.text
      status.state = snapshot.state
    }
  })

  if (data.event_type === 'outline') {
    upsertOutlineMessage(data, false)
  }
  if (data.event_type === 'interrupt') {
    upsertOutlineMessage(data, true)
  }
  if (data.event_type === 'artifact') {
    upsertResultMessage(data)
  }
  if (data.error) {
    error.value = data.error
  }
}

async function syncCurrentConversationFromServer() {
  if (!currentConversationId.value) {
    await refreshHistory()
    return
  }
  await refreshHistory({ selectConversationId: currentConversationId.value })
}

function normalizeChunk(value: unknown): PptStreamChunk {
  if (value && typeof value === 'object') {
    return value as PptStreamChunk
  }
  return { message: String(value) }
}

function upsertOutlineDelta(data: PptStreamChunk) {
  const requestId = data.request_id?.trim()
  const delta = data.text_delta ?? ''
  if (!requestId || !delta) {
    return
  }

  const existing = messages.value.find((item) => item.kind === 'outline' && item.requestId === requestId)
  if (existing && existing.kind === 'outline') {
    updateOutlineMessage(existing.id, (message) => {
      message.outlineMarkdown += delta
      message.title = outlineTitle(message.outlineMarkdown)
    })
    return
  }

  pushMessage({
    id: nextMessageId(),
    role: 'assistant',
    kind: 'outline',
    title: outlineTitle(delta),
    text: '正在生成 Markdown 大纲，确认后继续生成完整 PPT。',
    requestId,
    outlineMarkdown: delta,
    awaitingConfirm: false,
    showEditor: false,
  })
}

function upsertOutlineMessage(data: PptStreamChunk, awaitingConfirm: boolean) {
  const requestId = data.request_id?.trim()
  if (!requestId) {
    return
  }

  const outlineMarkdown = extractOutlineMarkdown(data)
  const existing = messages.value.find((item) => item.kind === 'outline' && item.requestId === requestId)

  if (existing && existing.kind === 'outline') {
    updateOutlineMessage(existing.id, (message) => {
      message.title = outlineTitle(outlineMarkdown || message.outlineMarkdown)
      message.text = '我先给你一版大纲，确认后继续生成完整 PPT。'
      if (outlineMarkdown) {
        message.outlineMarkdown = outlineMarkdown
      }
      message.title = outlineTitle(message.outlineMarkdown)
      message.awaitingConfirm = awaitingConfirm
    })
    return
  }

  pushMessage({
    id: nextMessageId(),
    role: 'assistant',
    kind: 'outline',
    title: outlineTitle(outlineMarkdown),
    text: '我先给你一版大纲，确认后继续生成完整 PPT。',
    requestId,
    outlineMarkdown,
    awaitingConfirm,
    showEditor: false,
  })
}

function upsertResultMessage(data: PptStreamChunk) {
  const requestId = data.request_id?.trim()
  if (!requestId) {
    return
  }

  const existing = messages.value.find((item) => item.kind === 'result' && item.requestId === requestId)
  if (existing) {
    return
  }

  pushMessage({
    id: nextMessageId(),
    role: 'assistant',
    kind: 'result',
    title: 'PPT 已准备好',
    text: '整套内容已经生成完成，你可以直接下载这份 PPT。',
    requestId,
    fileName: extractArtifactFileName(data) || `deck-${requestId}.pptx`,
  })
}

function extractOutlineMarkdown(data: PptStreamChunk) {
  if (typeof data.outline_markdown === 'string' && data.outline_markdown.trim()) {
    return data.outline_markdown
  }
  if (typeof data.content === 'string' && data.content.trim()) {
    return data.content
  }
  if (!data.payload || typeof data.payload !== 'object') {
    return ''
  }
  const payload = data.payload as Record<string, unknown>
  if (typeof payload.outline_markdown === 'string') {
    return payload.outline_markdown
  }
  if (typeof payload.outlineMarkdown === 'string') {
    return payload.outlineMarkdown
  }
  if (typeof payload.outline === 'string') {
    return payload.outline
  }
  return ''
}

function outlineTitle(markdown: string) {
  const heading = markdown
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line.startsWith('# '))
  return heading ? heading.replace(/^#\s+/, '').trim() || 'PPT 大纲' : 'PPT 大纲'
}

function extractArtifactFileName(data: PptStreamChunk) {
  if (!data.payload || typeof data.payload !== 'object') {
    return ''
  }
  const payload = data.payload as Record<string, unknown>
  return typeof payload.filename === 'string' ? payload.filename : ''
}

function describeChunk(data: PptStreamChunk) {
  if (data.error) {
    return {
      title: '生成失败',
      text: data.error,
      state: 'error' as const,
    }
  }

  if (data.event_type === 'interrupt') {
    return {
      title: '等待确认大纲',
      text: '我已经整理好一版大纲，确认后就继续生成完整 PPT。',
      state: 'waiting' as const,
    }
  }

  if (data.event_type === 'artifact' || data.event_type === 'done') {
    return {
      title: 'PPT 已生成',
      text: 'PPT 已经生成完成，可以直接下载。',
      state: 'done' as const,
    }
  }

  switch (data.stage) {
    case 'brief':
      return {
        title: '正在理解需求',
        text: '我在整理你的目标和上下文信息。',
        state: 'running' as const,
      }
    case 'outline':
      return {
        title: '正在规划大纲',
        text: '我在梳理 PPT 结构和每一页的重点。',
        state: 'running' as const,
      }
    case 'outline_review':
      return {
        title: '等待确认大纲',
        text: '大纲已经准备好，确认后我继续生成页面内容。',
        state: data.done ? ('waiting' as const) : ('running' as const),
      }
    case 'slides':
      return {
        title: '正在展开页面',
        text: '我在把大纲扩展成每一页的具体内容。',
        state: 'running' as const,
      }
    case 'review':
      return {
        title: '正在检查内容',
        text: '我在检查逻辑、重复信息和内容完整性。',
        state: 'running' as const,
      }
    case 'render':
      return {
        title: '正在导出 PPT',
        text: '正在把内容渲染成可下载的 PPT 文件。',
        state: 'running' as const,
      }
    default:
      return null
  }
}

function defaultStepMessage(data: PptStreamChunk) {
  if (data.error) {
    return data.error
  }
  return '已收到新的生成事件。'
}

function stageLabel(stage: string) {
  switch (stage) {
    case 'brief':
      return '需求理解'
    case 'outline':
      return '大纲规划'
    case 'outline_review':
      return '大纲确认'
    case 'slides':
      return '页面展开'
    case 'review':
      return '质量检查'
    case 'render':
      return 'PPT 导出'
    case 'artifact':
      return '下载结果'
    case 'done':
      return '完成'
    case 'error':
      return '错误'
    default:
      return '处理中'
  }
}

async function scrollThreadToBottom(smooth = true) {
  await nextTick()
  if (!threadRef.value) {
    return
  }
  threadRef.value.scrollTo({
    top: threadRef.value.scrollHeight,
    behavior: smooth ? 'smooth' : 'auto',
  })
}

async function downloadDeck(requestId: string, preferredName: string) {
  error.value = ''
  try {
    const result =
      typeof props.downloadArtifact === 'function'
        ? await props.downloadArtifact(requestId)
        : await fallbackDownloadArtifact(requestId)
    const url = URL.createObjectURL(result.blob)
    const link = document.createElement('a')
    link.href = url
    link.download = result.filename || preferredName || `deck-${requestId}.pptx`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function fallbackDownloadArtifact(requestId: string) {
  const session = readSessionFromStorage()
  if (!session?.access_token) {
    throw new Error('登录状态已失效，请重新登录后再下载。')
  }

  const response = await fetch(`/api/ppt-agent/artifacts/${encodeURIComponent(requestId)}`, {
    headers: {
      Authorization: `Bearer ${session.access_token}`,
    },
  })
  if (!response.ok) {
    throw new Error(`下载失败，状态码 ${response.status}。`)
  }

  return {
    blob: await response.blob(),
    filename: parseFilenameFromDisposition(response.headers.get('Content-Disposition')) || `deck-${requestId}.pptx`,
  }
}

function readSessionFromStorage() {
  try {
    const raw = localStorage.getItem('agent-task-platform-front:session')
    return raw ? (JSON.parse(raw) as { access_token?: string }) : null
  } catch {
    return null
  }
}

function parseFilenameFromDisposition(contentDisposition: string | null) {
  if (!contentDisposition) {
    return ''
  }
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1])
    } catch {
      return utf8Match[1]
    }
  }
  const plainMatch = contentDisposition.match(/filename="?([^"]+)"?/i)
  return plainMatch?.[1] ?? ''
}

function maxMessageId(items: ChatMessage[]) {
  return items.reduce((max, item) => Math.max(max, item.id || 0), 0)
}

function maxStepId(items: ChatMessage[]) {
  return items.reduce((max, item) => {
    if (item.kind !== 'status') {
      return max
    }
    return Math.max(max, item.steps.reduce((current, step) => Math.max(current, step.id || 0), 0))
  }, 0)
}

function statusStateLabel(state: StatusMessage['state']) {
  switch (state) {
    case 'running':
      return '进行中'
    case 'waiting':
      return '待确认'
    case 'done':
      return '已完成'
    case 'error':
      return '异常'
    default:
      return ''
  }
}

function formatHistoryTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}
</script>

<template>
  <section class="page chat-page">
    <div class="page-head chat-page-head">
      <div class="chat-page-copy">
        <span class="chat-page-kicker">PPT Workspace</span>
        <h1>PPT Assistant</h1>
        <p>像聊天一样描述需求，上传资料后我会先给你大纲，确认后再生成完整 PPT。</p>
      </div>
      <div v-if="props.configs.length > 0" class="chat-page-meta">
        <div class="page-meta-card">
          <span>当前配置</span>
          <strong>{{ selectedConfigHint }}</strong>
        </div>
        <div class="page-meta-card">
          <span>历史记录</span>
          <strong>{{ conversationCountLabel }}</strong>
        </div>
      </div>
    </div>

    <EmptyState
      v-if="props.configs.length === 0"
      :icon="Presentation"
      title="还没有可用的 PPT 配置"
      text="请先到 Agent 管理里创建一个 active 的 PPTAgent 配置，再回来发起生成。"
    />

    <div v-else class="chat-layout">
      <aside class="history-panel">
        <div class="history-panel-head">
          <div class="history-panel-copy">
            <span class="history-panel-kicker">History</span>
            <strong>历史记录</strong>
            <p>后端已保存你的 PPT 聊天记录。</p>
          </div>
          <button class="btn primary soft" type="button" :disabled="running" @click="startNewConversation">
            <MessageSquarePlus :size="16" />
            新对话
          </button>
        </div>

        <div v-if="historyLoading" class="history-empty">
          <Loader2 class="spin" :size="18" />
          <span>正在加载历史记录…</span>
        </div>

        <div v-else-if="conversationList.length === 0" class="history-empty">
          <FileClock :size="18" />
          <span>还没有历史记录，先发起一条新的 PPT 需求吧。</span>
        </div>

        <div v-else class="history-list">
          <button
            v-for="conversation in conversationList"
            :key="conversation.id"
            type="button"
            class="history-item"
            :class="{ active: currentConversationId === conversation.id }"
            :disabled="running"
            @click="openConversation(conversation.id)"
          >
            <div class="history-item-head">
              <strong>{{ conversation.title }}</strong>
              <span :class="['status-pill', `is-${conversation.status}`]">
                {{ statusStateLabel(normalizeStatusState(conversation.status)) }}
              </span>
            </div>
            <p>{{ conversation.agent_config_name }}</p>
            <small>{{ formatHistoryTime(conversation.updated_at) }}</small>
          </button>
        </div>
      </aside>

      <div class="chat-shell">
        <div class="chat-shell-header">
          <div>
            <span class="chat-shell-kicker">Conversation</span>
            <strong>{{ currentConversationSummary ? currentConversationSummary.title : '新的对话草稿' }}</strong>
            <p>
              {{
                currentConversationSummary
                  ? '继续查看之前的对话，或直接在下方发送新的修改需求。'
                  : '从一句自然语言开始，我会先帮你整理大纲。'
              }}
            </p>
          </div>
          <div class="chat-shell-badges">
            <span class="shell-badge">{{ selectedConfigHint }}</span>
            <span
              v-if="currentConversationSummary"
              class="shell-badge subtle"
            >
              {{ statusStateLabel(normalizeStatusState(currentConversationSummary.status)) }}
            </span>
          </div>
        </div>

        <section ref="threadRef" class="chat-thread">
          <article class="chat-message assistant">
            <div class="chat-avatar">AI</div>
            <div class="chat-bubble is-welcome">
              <div class="chat-label">PPT 助手</div>
              <strong>直接描述你想做的内容就行</strong>
              <p>你可以把页数、对象、风格、语气等要求自然写进一句话里，我会先整理大纲，再请你确认。</p>
              <span class="chat-note">
                {{ currentConversationSummary ? `当前查看：${currentConversationSummary.title}` : '当前是新的对话草稿。' }}
              </span>
            </div>
          </article>

          <article
            v-for="message in messages"
            :key="message.id"
            :class="['chat-message', message.role]"
          >
            <div class="chat-avatar">{{ message.role === 'assistant' ? 'AI' : '你' }}</div>

            <div
              :class="[
                'chat-bubble',
                `is-${message.kind}`,
                message.role === 'user' ? 'user-bubble' : 'assistant-bubble',
              ]"
            >
              <div class="chat-label">{{ message.role === 'assistant' ? 'PPT 助手' : '你' }}</div>

              <template v-if="message.kind === 'user'">
                <p class="chat-text preserve-lines">{{ message.text }}</p>
                <div v-if="message.fileName" class="file-chip readonly">
                  <Paperclip :size="14" />
                  <span>{{ message.fileName }}</span>
                </div>
              </template>

              <template v-else-if="message.kind === 'status'">
                <div class="status-header">
                  <strong>{{ message.title }}</strong>
                  <span :class="['status-pill', `is-${message.state}`]">{{ statusStateLabel(message.state) }}</span>
                </div>
                <p>{{ message.text }}</p>

                <details v-if="message.steps.length" class="status-details">
                  <summary>查看过程</summary>
                  <ol class="status-step-list">
                    <li v-for="step in message.steps" :key="step.id" :class="step.tone">
                      <span class="step-dot" />
                      <div>
                        <strong>{{ step.label }}</strong>
                        <p>{{ step.message }}</p>
                      </div>
                    </li>
                  </ol>
                </details>
              </template>

              <template v-else-if="message.kind === 'outline'">
                <div class="status-header">
                  <strong>{{ message.title }}</strong>
                  <span :class="['status-pill', message.awaitingConfirm ? 'is-waiting' : 'is-done']">
                    {{ message.awaitingConfirm ? '待确认' : '已确认' }}
                  </span>
                </div>
                <p>{{ message.text }}</p>

                <pre class="outline-preview markdown-preview">{{ message.outlineMarkdown }}</pre>

                <textarea
                  v-if="message.showEditor"
                  v-model="message.outlineMarkdown"
                  class="outline-editor"
                  rows="12"
                  spellcheck="false"
                />

                <div v-if="message.awaitingConfirm" class="outline-actions">
                  <button class="btn primary" type="button" :disabled="running" @click="confirmOutline(message)">
                    <CheckCircle2 :size="18" />
                    确认继续生成
                  </button>
                  <button
                    class="btn ghost"
                    type="button"
                    :disabled="running"
                    @click="message.showEditor = !message.showEditor"
                  >
                    {{ message.showEditor ? '收起大纲' : '编辑大纲' }}
                  </button>
                </div>
              </template>

              <template v-else-if="message.kind === 'result'">
                <div class="status-header">
                  <strong>{{ message.title }}</strong>
                  <span class="status-pill is-done">已完成</span>
                </div>
                <p>{{ message.text }}</p>

                <div class="result-actions">
                  <button class="btn success" type="button" @click="downloadDeck(message.requestId, message.fileName)">
                    <Download :size="18" />
                    下载 PPT
                  </button>
                  <span class="chat-note">{{ message.fileName }}</span>
                </div>
              </template>
            </div>
          </article>
        </section>

        <form class="composer-panel" @submit.prevent="submitPrompt">
          <div class="composer-frame">
            <div class="composer-box">
              <textarea
                v-model="composer.text"
                rows="4"
                placeholder="例如：帮我做一份 AI Agent 平台 MVP 汇报，面向投资人，风格简洁专业，控制在 10 页左右。"
              />

              <div v-if="composer.file" class="file-chip">
                <div class="file-chip-main">
                  <Paperclip :size="14" />
                  <span>{{ composer.file.name }}</span>
                </div>
                <button class="chip-close" type="button" @click="clearSelectedFile">
                  <X :size="14" />
                </button>
              </div>
            </div>

            <div class="composer-divider" />

            <p v-if="error" class="form-error composer-error">
              <AlertCircle :size="16" />
              {{ error }}
            </p>

            <div class="composer-toolbar">
              <div class="composer-actions">
                <label class="btn ghost file-picker">
                  <Paperclip :size="16" />
                  添加资料
                  <input
                    ref="fileInput"
                    type="file"
                    accept=".pdf,.txt,.md,.markdown,.csv,.json,.yaml,.yml,.docx,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    @change="onSourceFileChange"
                  />
                </label>

                <label class="config-picker">
                  <span>使用配置</span>
                  <select v-model="selectedConfigId" :disabled="running">
                    <option v-for="config in props.configs" :key="config.id" :value="String(config.id)">
                      {{ config.name }}{{ config.is_default ? ' / 默认' : '' }}
                    </option>
                  </select>
                </label>
              </div>

              <div class="composer-footnote">
                <span class="chat-note">支持 PDF、DOCX、Markdown、TXT 等文件。</span>
              </div>

              <div class="composer-toolbar-actions">
                <button v-if="running" class="btn ghost" type="button" @click="stop">
                  <Square :size="18" />
                  停止
                </button>
                <button class="btn primary send-btn" type="submit" :disabled="!canSubmit">
                  <Loader2 v-if="running" class="spin" :size="18" />
                  <span v-else>发送需求</span>
                  <span v-if="running">生成中</span>
                </button>
              </div>
            </div>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>
