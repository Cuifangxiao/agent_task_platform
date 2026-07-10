<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  AlertCircle,
  CheckCircle2,
  Download,
  FileClock,
  Loader2,
  Maximize2,
  MessageSquarePlus,
  Paperclip,
  Presentation,
  Square,
  X,
} from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
import { assetCacheKey, eventSequenceDecision, interactionPhase } from '@/pptAgentWorkflow'
import type {
  AgentConfig,
  ParsedSseEvent,
  PptConversationDetail,
  PptConversationSummary,
  PptDeckJob,
  PptGeneratePayload,
  PptResumePayload,
  PptStreamChunk,
  PptJobActionPayload,
} from '@/types'

const props = defineProps<{
  configs: AgentConfig[]
  listConversations: () => Promise<PptConversationSummary[]>
  loadConversation: (id: number) => Promise<PptConversationDetail>
  downloadArtifact?: (requestId: string) => Promise<{ blob: Blob; filename: string }>
  streamPpt: (payload: PptGeneratePayload, onEvent: (event: ParsedSseEvent) => void, signal: AbortSignal) => Promise<void>
  resumePpt: (payload: PptResumePayload, onEvent: (event: ParsedSseEvent) => void, signal: AbortSignal) => Promise<void>
  createJob: (payload: PptGeneratePayload) => Promise<PptDeckJob>
  getJob: (jobId: string) => Promise<PptDeckJob>
  subscribeJob: (jobId: string, onEvent: (event: ParsedSseEvent) => void, signal: AbortSignal, lastEventId?: number) => Promise<void>
  submitJobAction: (jobId: string, payload: PptJobActionPayload) => Promise<{ job: PptDeckJob; duplicate: boolean }>
  cancelJob: (jobId: string) => Promise<PptDeckJob>
}>()

interface ProgressStep {
  id: number
  label: string
  message: string
  stage?: string
  tone: 'normal' | 'error' | 'done'
}

type SlideProgressStatus = 'pending' | 'running' | 'ready' | 'reviewed' | 'warning' | 'error'

interface SlideProgress {
  index: number
  title: string
  keyMessage: string
  layout: string
  component: string
  bullets: string[]
  visualSuggestion: string
  speakerNotes: string
  previewPng: string
  previewHtml: string
  previewThumb: string
  version: number
  status: SlideProgressStatus
  warnings: string[]
}

interface SlideProgressUpdate {
  index: number
  title?: string
  keyMessage?: string
  layout?: string
  component?: string
  bullets?: string[]
  visualSuggestion?: string
  speakerNotes?: string
  previewPng?: string
  previewHtml?: string
  previewThumb?: string
  version?: number
  status?: SlideProgressStatus
  warnings?: string[]
}

interface StyleOption {
  styleId: string
  name: string
  coverPng: string
  coverHtml: string
  thumbB64: string
  tokensSummary: string
}

interface UserMessage {
  id: number
  role: 'user'
  kind: 'user'
  text: string
  fileNames?: string[]
}

interface StatusMessage {
  id: number
  role: 'assistant'
  kind: 'status'
  title: string
  text: string
  requestId?: string
  state: 'running' | 'waiting' | 'done' | 'error' | 'cancelled'
  steps: ProgressStep[]
  slides: SlideProgress[]
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

interface StyleMessage {
  id: number
  role: 'assistant'
  kind: 'style'
  title: string
  text: string
  requestId: string
  options: StyleOption[]
  awaitingConfirm: boolean
}

interface ResultMessage {
  id: number
  role: 'assistant'
  kind: 'result'
  title: string
  text: string
  requestId: string
  fileName: string
  warnings: string[]
  degraded: boolean
  qualityStatus: string
}

type ChatMessage = UserMessage | StatusMessage | OutlineMessage | StyleMessage | ResultMessage

interface PreviewLightbox {
  src: string
  title: string
  subtitle: string
}

type PipelineStageStatus = 'done' | 'running' | 'waiting' | 'pending' | 'error'

interface PipelineStageView {
  key: string
  label: string
  description: string
  status: PipelineStageStatus
}

const pipelineStages = [
  { key: 'brief', label: '理解需求', description: '整理目标、受众和上下文' },
  { key: 'outline', label: '规划大纲', description: '生成 PPT 结构和每页重点' },
  { key: 'outline_review', label: '确认大纲', description: '等待你确认或调整大纲' },
  { key: 'style', label: '准备风格', description: '生成视觉方向和封面样张' },
  { key: 'style_review', label: '选择风格', description: '等待选择视觉方向' },
  { key: 'writer', label: '撰写页面', description: '把大纲扩展成逐页结构化内容' },
  { key: 'designer', label: '设计页面', description: '选择版式并渲染 HTML 和 PNG 预览' },
  { key: 'validate', label: '质量校验', description: '检查版式、密度和结构' },
  { key: 'repair', label: '自动修复', description: '修复校验发现的问题' },
  { key: 'review_vlm', label: '视觉复核', description: '检查视觉质量和完整性' },
  { key: 'coherence', label: '统一表达', description: '检查跨页连贯性' },
  { key: 'export', label: '导出 PPT', description: '生成可下载的 PPT 文件' },
  { key: 'artifact', label: '完成结果', description: '准备下载文件' },
] as const

const composer = reactive({
  text: '',
  files: [] as File[],
})

const abortController = ref<AbortController | null>(null)
const conversationList = ref<PptConversationSummary[]>([])
const currentConversationId = ref<number | null>(null)
const currentStatusMessageId = ref<number | null>(null)
const error = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const historyLoading = ref(false)
const messages = ref<ChatMessage[]>([])
const previewObjectUrls = reactive<Record<string, string>>({})
const previewLightbox = ref<PreviewLightbox | null>(null)
const running = ref(false)
const currentJobId = ref('')
const lastEventSequence = ref(0)
const generationMode = ref<'quick' | 'step_by_step'>('step_by_step')
const uploadState = ref('')
const styleFeedback = reactive<Record<number, string>>({})
const slideFeedback = reactive<Record<number, string>>({})
const selectedConfigId = ref('')
const threadRef = ref<HTMLElement | null>(null)

let messageSeed = 0
const previewFailures = new Set<string>()
const previewLoads = new Set<string>()
const seenEventSequences = new Set<number>()
let stepSeed = 0

const defaultConfig = computed(() => props.configs.find((item) => item.is_default) ?? props.configs[0] ?? null)
const selectedConfig = computed(() => props.configs.find((item) => String(item.id) === selectedConfigId.value) ?? null)
const canSubmit = computed(() => Boolean(selectedConfig.value) && composer.text.trim().length > 0 && !running.value && messages.value.length === 0)
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

onBeforeUnmount(() => {
  abortController.value?.abort()
  Object.values(previewObjectUrls).forEach((url) => URL.revokeObjectURL(url))
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
  currentJobId.value = ''
  lastEventSequence.value = 0
  seenEventSequences.clear()
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
  if (running.value && currentConversationId.value && currentConversationId.value !== id) {
    return
  }

  const detail = await props.loadConversation(id)
  currentConversationId.value = detail.id
  selectedConfigId.value = String(detail.agent_config_id)
  messages.value = detail.messages.map(normalizeStoredMessage)
  messageSeed = maxMessageId(messages.value)
  stepSeed = maxStepId(messages.value)
  currentStatusMessageId.value = null
  currentJobId.value = detail.job?.job_id ?? ''
  lastEventSequence.value = detail.job?.last_event_sequence ?? 0
  seenEventSequences.clear()
  const latestStatus = [...messages.value].reverse().find((item): item is StatusMessage => item.kind === 'status')
  currentStatusMessageId.value = latestStatus?.id ?? null
  running.value = isActiveJob(detail.job)
  error.value = ''
  clearSelectedFile()
  await scrollThreadToBottom(false)
  if (detail.job && isActiveJob(detail.job)) {
    void followJob(detail.job.job_id, detail.job.last_event_sequence)
  }
}

function isActiveJob(job: PptDeckJob | undefined) {
  if (!job) return false
  return ['initial', 'pages'].includes(interactionPhase(job.status))
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
      fileNames: normalizeStoredFileNames(item),
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

  if (kind === 'style') {
    return {
      id,
      role: 'assistant',
      kind: 'style',
      title: String(item.title ?? '选择 PPT 风格'),
      text: String(item.text ?? ''),
      requestId: String(item.requestId ?? ''),
      options: normalizeStyleOptions(item.options),
      awaitingConfirm: Boolean(item.awaitingConfirm),
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
      warnings: normalizeStringList(item.warnings),
      degraded: Boolean(item.degraded),
      qualityStatus: String(item.qualityStatus ?? ''),
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
    slides: normalizeStoredSlides(item.slides),
  }
}

function normalizeStoredFileNames(item: Record<string, unknown>) {
  if (Array.isArray(item.fileNames)) {
    return item.fileNames.map((fileName) => String(fileName)).filter(Boolean)
  }
  return typeof item.fileName === 'string' && item.fileName ? [item.fileName] : []
}

function normalizeStatusState(value: unknown): StatusMessage['state'] {
  switch (value) {
    case 'running':
    case 'waiting':
    case 'done':
    case 'error':
      return value
    case 'cancelled':
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
      stage: typeof record.stage === 'string' ? normalizeProgressStage(record.stage) : undefined,
      tone: normalizeStepTone(record.tone),
    }
  })
}

function normalizeStoredSlides(value: unknown): SlideProgress[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value
    .map((item) => {
      const record = item && typeof item === 'object' ? (item as Record<string, unknown>) : {}
      const index = Number(record.index)
      if (!Number.isFinite(index) || index <= 0) {
        return null
      }
      return {
        index,
        title: String(record.title ?? `Slide ${index}`),
        keyMessage: String(record.keyMessage ?? record.key_message ?? ''),
        layout: String(record.layout ?? 'content'),
        component: String(record.component ?? ''),
        bullets: normalizeStringList(record.bullets),
        visualSuggestion: String(record.visualSuggestion ?? record.visual_suggestion ?? ''),
        speakerNotes: String(record.speakerNotes ?? record.speaker_notes ?? ''),
        previewPng: String(record.previewPng ?? record.png ?? ''),
        previewHtml: String(record.previewHtml ?? record.html ?? ''),
        previewThumb: String(record.previewThumb ?? record.thumb_b64 ?? ''),
        version: Number(record.version ?? 0),
        status: normalizeSlideStatus(record.status),
        warnings: normalizeStringList(record.warnings),
      }
    })
    .filter((item): item is SlideProgress => Boolean(item))
    .sort((left, right) => left.index - right.index)
}

function normalizeStyleOptions(value: unknown): StyleOption[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value
    .map((item) => {
      const record = item && typeof item === 'object' ? (item as Record<string, unknown>) : {}
      const styleId = String(record.style_id ?? record.styleId ?? '')
      if (!styleId) {
        return null
      }
      return {
        styleId,
        name: String(record.name ?? styleId),
        coverPng: String(record.cover_png ?? record.coverPng ?? ''),
        coverHtml: String(record.cover_html ?? record.coverHtml ?? ''),
        thumbB64: String(record.thumb_b64 ?? record.thumbB64 ?? ''),
        tokensSummary: String(record.tokens_summary ?? record.tokensSummary ?? ''),
      }
    })
    .filter((item): item is StyleOption => Boolean(item))
}

function normalizeStepTone(value: unknown): ProgressStep['tone'] {
  return value === 'error' || value === 'done' ? value : 'normal'
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.map((item) => String(item).trim()).filter(Boolean)
}

function normalizeSlideStatus(value: unknown): SlideProgressStatus {
  switch (value) {
    case 'pending':
    case 'running':
    case 'ready':
    case 'reviewed':
    case 'warning':
    case 'error':
      return value
    case 'ok':
      return 'reviewed'
    default:
      return 'pending'
  }
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

  if (messages.value.some((item) => item.kind === 'result')) {
    error.value = '当前整套修改需先点“新建对话”；已有 Deck 请在具体页面使用“重做此页”。'
    return
  }
  if (currentConversationId.value || messages.value.length > 0) {
    resetDraftConversation(true)
  }

  const selectedFiles = [...composer.files]
  const fileError = validateSourceFiles(selectedFiles)
  if (fileError) {
    error.value = fileError
    return
  }
  const fileNames = selectedFiles.map((file) => file.name)

  pushMessage({
    id: nextMessageId(),
    role: 'user',
    kind: 'user',
    text: description,
    ...(fileNames.length ? { fileNames } : {}),
  })

  currentStatusMessageId.value = createStatusMessage(
    '正在规划大纲',
    '我先根据你的描述整理一版 PPT 大纲，稍后发给你确认。',
  )

  const payload: PptGeneratePayload = {
    agent_config_id: config.id,
    description,
    source_files: selectedFiles,
    mode: generationMode.value,
  }

  composer.text = ''
  clearSelectedFile()
  running.value = true
  const uploadStartedAt = performance.now()
  try {
    uploadState.value = selectedFiles.length ? '正在上传并创建后台任务…' : '正在创建后台任务…'
    const job = await props.createJob(payload)
    currentJobId.value = job.job_id
    currentConversationId.value = job.conversation_id
    lastEventSequence.value = 0
    seenEventSequences.clear()
    uploadState.value = selectedFiles.length ? '附件已上传，等待解析。' : '后台任务已创建。'
    if (currentStatusMessageId.value) {
      const uploadDuration = (performance.now() - uploadStartedAt) / 1000
      updateStatusMessage(currentStatusMessageId.value, (status) => {
        status.steps.push({
          id: nextStepId(),
          label: selectedFiles.length ? '附件上传' : '任务创建',
          message: `${selectedFiles.length ? '附件上传完成' : '后台任务创建完成'} · ${uploadDuration.toFixed(1)}s`,
          stage: 'brief',
          tone: 'done',
        })
      })
    }
    await followJob(job.job_id, 0)
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      const message = err instanceof Error ? err.message : String(err)
      error.value = message
      markCurrentRunAsFailed(message)
    }
  } finally {
    uploadState.value = ''
    await syncCurrentConversationFromServer()
  }
}

async function followJob(jobId: string, afterSequence: number) {
  if (!jobId || abortController.value) {
    return
  }
  const controller = new AbortController()
  abortController.value = controller
  running.value = true
  try {
    await props.subscribeJob(jobId, handleEvent, controller.signal, afterSequence)
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      const job = await props.getJob(jobId).catch(() => null)
      if (job && isActiveJob(job)) {
        error.value = '进度连接已断开，后台任务仍在运行，正在自动恢复。'
        window.setTimeout(() => void followJob(jobId, lastEventSequence.value), 750)
      } else if (job?.status === 'failed') {
        markCurrentRunAsFailed(job.error_summary || '生成失败。')
      }
    }
  } finally {
    if (abortController.value === controller) {
      abortController.value = null
    }
    const job = await props.getJob(jobId).catch(() => null)
    running.value = Boolean(job && isActiveJob(job))
    if (job?.status === 'cancelled') {
      markCurrentRunAsStopped('任务已取消。')
    }
  }
}

function validateSourceFiles(files: File[]) {
  const allowed = /\.(pdf|txt|md|markdown|csv|json|ya?ml|docx)$/i
  const maxSingle = 20 * 1024 * 1024
  const maxTotal = 100 * 1024 * 1024
  let total = 0
  for (const file of files) {
    if (!allowed.test(file.name)) {
      return `不支持 ${file.name} 的格式。请上传 PDF、DOCX、Markdown、TXT、CSV、JSON 或 YAML。`
    }
    if (file.size > maxSingle) {
      return `${file.name} 超过 20 MB 单文件限制。`
    }
    total += file.size
  }
  return total > maxTotal ? '附件总大小不能超过 100 MB。' : ''
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
    slides: [],
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

function slideProgressFromChunk(data: PptStreamChunk): SlideProgressUpdate | null {
  const trackedEvents = new Set([
    'slide_started',
    'slide_spec_ready',
    'slide_preview',
    'slide_ready',
    'slide_reviewed',
    'stage_progress',
  ])
  if (!trackedEvents.has(data.event_type ?? '')) {
    return null
  }
  if (data.event_type === 'stage_progress' && !['slides', 'writer', 'designer', 'repair'].includes(data.stage ?? '')) {
    return null
  }
  if (!data.payload || typeof data.payload !== 'object') {
    return null
  }

  const payload = data.payload as Record<string, unknown>
  const index = Number(payload.index)
  if (!Number.isFinite(index) || index <= 0) {
    return null
  }

  const warnings = normalizeStringList(payload.warnings)
  if (typeof payload.warning === 'string' && payload.warning.trim()) {
    warnings.push(payload.warning.trim())
  }

  const statusFromEvent =
    data.event_type === 'slide_started'
      ? 'running'
      : data.event_type === 'slide_spec_ready'
        ? 'running'
        : data.event_type === 'slide_preview' || data.event_type === 'slide_ready'
        ? 'ready'
        : data.event_type === 'slide_reviewed' && warnings.length > 0
          ? 'warning'
          : undefined

  return {
    index,
    title: typeof payload.title === 'string' ? payload.title : undefined,
    keyMessage: typeof payload.key_message === 'string' ? payload.key_message : undefined,
    layout: typeof payload.layout === 'string' ? payload.layout : undefined,
    component: typeof payload.component === 'string' ? payload.component : undefined,
    bullets: Array.isArray(payload.bullets) ? normalizeStringList(payload.bullets) : undefined,
    visualSuggestion: typeof payload.visual_suggestion === 'string' ? payload.visual_suggestion : undefined,
    speakerNotes: typeof payload.speaker_notes === 'string' ? payload.speaker_notes : undefined,
    previewPng: typeof payload.png === 'string' ? payload.png : undefined,
    previewHtml: typeof payload.html === 'string' ? payload.html : undefined,
    previewThumb: typeof payload.thumb_b64 === 'string' ? payload.thumb_b64 : undefined,
    version: Number.isFinite(Number(payload.version)) ? Number(payload.version) : undefined,
    status: normalizeSlideStatus(payload.status ?? statusFromEvent),
    warnings,
  }
}

function upsertSlideProgress(status: StatusMessage, update: SlideProgressUpdate) {
  let slide = status.slides.find((item) => item.index === update.index)
  if (!slide) {
    slide = {
      index: update.index,
      title: update.title || `Slide ${update.index}`,
      keyMessage: '',
      layout: 'content',
      component: '',
      bullets: [],
      visualSuggestion: '',
      speakerNotes: '',
      previewPng: '',
      previewHtml: '',
      previewThumb: '',
      version: 0,
      status: 'pending',
      warnings: [],
    }
    status.slides.push(slide)
  }

  if (update.title !== undefined) {
    slide.title = update.title || slide.title
  }
  if (update.keyMessage !== undefined) {
    slide.keyMessage = update.keyMessage
  }
  if (update.layout !== undefined) {
    slide.layout = update.layout || slide.layout
  }
  if (update.component !== undefined) {
    slide.component = update.component || slide.component
  }
  if (update.bullets !== undefined) {
    slide.bullets = update.bullets
  }
  if (update.visualSuggestion !== undefined) {
    slide.visualSuggestion = update.visualSuggestion
  }
  if (update.speakerNotes !== undefined) {
    slide.speakerNotes = update.speakerNotes
  }
  if (update.previewPng !== undefined) {
    slide.previewPng = update.previewPng
  }
  if (update.previewHtml !== undefined) {
    slide.previewHtml = update.previewHtml
  }
  if (update.previewThumb !== undefined) {
    slide.previewThumb = update.previewThumb
  }
  if (update.version !== undefined) {
    slide.version = update.version
  }
  if (update.status !== undefined && update.status !== 'pending') {
    slide.status = update.status
  }
  if (update.warnings?.length) {
    slide.warnings = [...new Set([...slide.warnings, ...update.warnings])]
    if (slide.status !== 'error') {
      slide.status = 'warning'
    }
  }

  status.slides.sort((left, right) => left.index - right.index)
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

function updateStyleMessage(id: number, updater: (message: StyleMessage) => void) {
  const index = messages.value.findIndex((item) => item.id === id && item.kind === 'style')
  if (index < 0) {
    return
  }
  const message = messages.value[index]
  if (message.kind !== 'style') {
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
    status.state = 'cancelled'
    status.title = '已取消'
    status.text = message
    status.steps.push({
      id: nextStepId(),
      label: '已停止',
      message,
      stage: 'error',
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
      stage: 'error',
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

  try {
    if (!currentJobId.value) {
      throw new Error('缺少可恢复的 job_id，请刷新对话后重试。')
    }
    const after = lastEventSequence.value
    await props.submitJobAction(currentJobId.value, {
        action: 'approve_outline',
        outline_markdown: outlineMarkdown,
    })
    await followJob(currentJobId.value, after)
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      const failure = err instanceof Error ? err.message : String(err)
      error.value = failure
      markCurrentRunAsFailed(failure)
    }
  } finally {
    await syncCurrentConversationFromServer()
  }
}

async function regenerateOutline(message: OutlineMessage) {
  if (!currentJobId.value) {
    error.value = '缺少 job_id，请刷新对话后重试。'
    return
  }
  updateOutlineMessage(message.id, (draft) => { draft.awaitingConfirm = false })
  currentStatusMessageId.value = createStatusMessage('重新生成大纲', '我正在重新规划叙事结构和页面重点。')
  const after = lastEventSequence.value
  running.value = true
  try {
    await props.submitJobAction(currentJobId.value, { action: 'regenerate_outline' })
    await followJob(currentJobId.value, after)
  } catch (err) {
    const failure = err instanceof Error ? err.message : String(err)
    error.value = failure
    markCurrentRunAsFailed(failure)
  } finally {
    await syncCurrentConversationFromServer()
  }
}

async function chooseStyle(message: StyleMessage, option: StyleOption) {
  error.value = ''
  updateStyleMessage(message.id, (draft) => {
    draft.awaitingConfirm = false
  })

  pushMessage({
    id: nextMessageId(),
    role: 'user',
    kind: 'user',
    text: `选择风格：${option.name}${styleFeedback[message.id]?.trim() ? `；调整意见：${styleFeedback[message.id].trim()}` : ''}`,
  })

  currentStatusMessageId.value = createStatusMessage(
    '生成页面预览',
    '我正在按选定风格生成每一页的 HTML 预览。',
  )
  running.value = true

  try {
    if (!currentJobId.value) {
      throw new Error('缺少可恢复的 job_id，请刷新对话后重试。')
    }
    const after = lastEventSequence.value
    await props.submitJobAction(currentJobId.value, {
        action: 'choose_style',
        payload: { style_id: option.styleId, feedback: styleFeedback[message.id]?.trim() ?? '' },
    })
    await followJob(currentJobId.value, after)
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      const failure = err instanceof Error ? err.message : String(err)
      error.value = failure
      markCurrentRunAsFailed(failure)
    }
  } finally {
    await syncCurrentConversationFromServer()
  }
}

async function stop() {
  if (!currentJobId.value) {
    abortController.value?.abort()
    return
  }
  try {
    await props.cancelJob(currentJobId.value)
    abortController.value?.abort()
    markCurrentRunAsStopped('任务已取消。')
    running.value = false
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function onSourceFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  composer.files = [...composer.files, ...Array.from(input.files ?? [])]
  input.value = ''
  error.value = ''
}

function removeSelectedFile(index: number) {
  composer.files = composer.files.filter((_, currentIndex) => currentIndex !== index)
}

function clearSelectedFile() {
  composer.files = []
  if (fileInput.value) {
    fileInput.value.value = ''
  }
}

function handleEvent(event: ParsedSseEvent) {
  const data = normalizeChunk(event.data)
  const sequence = Number(event.id ?? data.sequence ?? 0)
  if (sequence > 0) {
    const decision = eventSequenceDecision(lastEventSequence.value, seenEventSequences, sequence)
    if (!decision.accept) {
      return
    }
    seenEventSequences.add(sequence)
    lastEventSequence.value = decision.last
  }
  if (data.job_id) {
    currentJobId.value = data.job_id
  }
  if (data.event_type === 'fact_sheet_ready') {
    uploadState.value = '附件解析完成，事实表已建立。'
  }
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

  const slideUpdate = slideProgressFromChunk(data)
  updateStatusMessage(statusId, (status) => {
    if (data.request_id) {
      status.requestId = data.request_id
    }
    if (slideUpdate) {
      upsertSlideProgress(status, slideUpdate)
    }
    for (const warningUpdate of qualityWarningUpdates(data)) {
      upsertSlideProgress(status, warningUpdate)
    }
    status.steps.push({
      id: nextStepId(),
      label: eventStepLabel(data),
      message: displayStepMessage(data),
      stage: normalizeProgressStage(data.stage || data.event_type || 'progress'),
      tone:
        data.stage_status === 'error' || data.error
          ? 'error'
          : data.stage_status === 'done' || data.done
            ? 'done'
            : 'normal',
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
  if (data.event_type === 'style_options') {
    upsertStyleMessage(data, false)
  }
  if (data.event_type === 'interrupt' && data.stage === 'style_review') {
    upsertStyleMessage(data, true)
  }
  if (data.event_type === 'interrupt' && data.stage !== 'style_review') {
    upsertOutlineMessage(data, true)
  }
  if (data.event_type === 'artifact') {
    upsertResultMessage(data)
  }
  if (data.error) {
    error.value = data.error
  }
}

function qualityWarningUpdates(data: PptStreamChunk): SlideProgressUpdate[] {
  if (data.event_type !== 'validation_report' || !data.payload || typeof data.payload !== 'object') {
    return []
  }
  const issues = (data.payload as Record<string, unknown>).issues
  if (!Array.isArray(issues)) return []
  const byPage = new Map<number, { warnings: string[]; mustFix: boolean }>()
  for (const raw of issues) {
    if (!raw || typeof raw !== 'object') continue
    const issue = raw as Record<string, unknown>
    const page = Number(issue.page)
    const description = String(issue.description ?? '').trim()
    if (!Number.isFinite(page) || page <= 0 || !description) continue
    const current = byPage.get(page) ?? { warnings: [], mustFix: false }
    current.warnings.push(description)
    current.mustFix ||= issue.severity === 'must_fix'
    byPage.set(page, current)
  }
  return [...byPage.entries()].map(([index, value]) => ({
    index,
    warnings: [...new Set(value.warnings)],
    status: value.mustFix ? 'error' : 'warning',
  }))
}

async function regenerateSlide(slide: SlideProgress) {
  if (!currentJobId.value) {
    error.value = '缺少 job_id，请刷新对话后重试。'
    return
  }
  const feedback = slideFeedback[slide.index]?.trim() ?? ''
  if (!feedback) {
    error.value = `请先填写第 ${slide.index} 页的修改意见。`
    return
  }
  error.value = ''
  currentStatusMessageId.value = createStatusMessage('重做单页', `正在按反馈重做第 ${slide.index} 页。`)
  const after = lastEventSequence.value
  running.value = true
  try {
    await props.submitJobAction(currentJobId.value, {
      action: 'regenerate_slide',
      payload: { index: slide.index, feedback },
    })
    await followJob(currentJobId.value, after)
  } catch (err) {
    const failure = err instanceof Error ? err.message : String(err)
    error.value = failure
    markCurrentRunAsFailed(failure)
  } finally {
    await syncCurrentConversationFromServer()
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

function upsertStyleMessage(data: PptStreamChunk, awaitingConfirm: boolean) {
  const requestId = data.request_id?.trim()
  if (!requestId) {
    return
  }
  const options = extractStyleOptions(data)
  const existing = messages.value.find((item) => item.kind === 'style' && item.requestId === requestId)
  if (existing && existing.kind === 'style') {
    updateStyleMessage(existing.id, (message) => {
      if (options.length) {
        message.options = options
      }
      message.awaitingConfirm = awaitingConfirm
    })
    return
  }
  pushMessage({
    id: nextMessageId(),
    role: 'assistant',
    kind: 'style',
    title: '选择 PPT 风格',
    text: '我准备了两套视觉方向，选择后继续生成页面。',
    requestId,
    options,
    awaitingConfirm,
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
    warnings: normalizeArtifactWarnings(data),
    degraded: artifactBoolean(data, 'degraded'),
    qualityStatus: artifactString(data, 'quality_status'),
  })
}

function normalizeArtifactWarnings(data: PptStreamChunk) {
  if (!data.payload || typeof data.payload !== 'object') return []
  const payload = data.payload as Record<string, unknown>
  const warnings = normalizeStringList(payload.warnings)
  if (Array.isArray(payload.issues)) {
    for (const raw of payload.issues) {
      if (!raw || typeof raw !== 'object') continue
      const issue = raw as Record<string, unknown>
      const description = String(issue.description ?? '').trim()
      if (!description) continue
      const page = Number(issue.page)
      warnings.push(`${Number.isFinite(page) && page > 0 ? `第 ${page} 页：` : '全局：'}${description}`)
    }
  }
  return [...new Set(warnings)]
}

function artifactBoolean(data: PptStreamChunk, key: string) {
  return Boolean(data.payload && typeof data.payload === 'object' && (data.payload as Record<string, unknown>)[key])
}

function artifactString(data: PptStreamChunk, key: string) {
  return data.payload && typeof data.payload === 'object' ? String((data.payload as Record<string, unknown>)[key] ?? '') : ''
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

function extractStyleOptions(data: PptStreamChunk): StyleOption[] {
  if (!data.payload || typeof data.payload !== 'object') {
    return []
  }
  const payload = data.payload as Record<string, unknown>
  return normalizeStyleOptions(payload.options ?? payload.style_options)
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
    if (data.stage === 'style_review') {
      return {
        title: '等待选择风格',
        text: '两套视觉方向已经准备好，选择后继续生成页面。',
        state: 'waiting' as const,
      }
    }
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
    case 'style':
      return {
        title: '正在生成风格样张',
        text: '我在准备两套视觉方向。',
        state: 'running' as const,
      }
    case 'style_review':
      return {
        title: '等待选择风格',
        text: '请选择一套视觉方向继续。',
        state: data.done ? ('waiting' as const) : ('running' as const),
      }
    case 'slides':
    case 'writer':
      return {
        title: '正在展开页面',
        text: '我在把大纲扩展成每一页的具体内容。',
        state: 'running' as const,
      }
    case 'design_slides':
    case 'designer':
      return {
        title: '正在生成页面预览',
        text: '我在把结构化内容渲染成 HTML 与 PNG 预览。',
        state: 'running' as const,
      }
    case 'validate':
      return {
        title: '正在校验页面',
        text: '我在检查占位、密度和页面结构。',
        state: 'running' as const,
      }
    case 'coherence':
      return {
        title: '正在统一全局表达',
        text: '我在检查跨页连贯性。',
        state: 'running' as const,
      }
    case 'review':
    case 'review_vlm':
      return {
        title: '正在检查内容',
        text: '我在检查逻辑、重复信息和内容完整性。',
        state: 'running' as const,
      }
    case 'render':
    case 'export':
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

function displayStepMessage(data: PptStreamChunk) {
  const base = data.message || defaultStepMessage(data)
  if (!data.payload || typeof data.payload !== 'object') return base
  const metrics = (data.payload as Record<string, unknown>).metrics
  if (!metrics || typeof metrics !== 'object') return base
  const values = metrics as Record<string, unknown>
  const duration = Number(values.duration_ms)
  const details: string[] = []
  if (Number.isFinite(duration) && duration >= 0) details.push(`${(duration / 1000).toFixed(1)}s`)
  const model = [values.provider, values.model].filter((item) => typeof item === 'string' && item).join('/')
  if (model) details.push(model)
  const tokens = Number(values.token_usage)
  if (Number.isFinite(tokens) && tokens > 0) details.push(`${tokens} tokens`)
  const retries = Number(values.retry_count)
  if (Number.isFinite(retries) && retries > 0) details.push(`重试 ${retries}`)
  const fallbacks = Number(values.fallback_count)
  if (Number.isFinite(fallbacks) && fallbacks > 0) details.push(`降级 ${fallbacks}`)
  return details.length ? `${base} · ${details.join(' · ')}` : base
}

function eventStepLabel(data: PptStreamChunk) {
  if (data.payload && typeof data.payload === 'object' && (data.payload as Record<string, unknown>).phase === 'parse') {
    return '附件解析'
  }
  return stageLabel(data.stage || data.event_type || 'progress')
}

function stageLabel(stage: string) {
  switch (stage) {
    case 'brief':
      return '需求理解'
    case 'outline':
      return '大纲规划'
    case 'outline_review':
      return '大纲确认'
    case 'style':
      return '风格样张'
    case 'style_review':
      return '风格选择'
    case 'slides':
    case 'writer':
      return '页面展开'
    case 'design_slides':
    case 'designer':
      return '页面预览'
    case 'validate':
      return '质量校验'
    case 'coherence':
      return '连贯检查'
    case 'review':
    case 'review_vlm':
      return '质量检查'
    case 'render':
    case 'export':
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

function normalizeProgressStage(value: string | undefined) {
  switch (value) {
    case 'brief':
    case 'outline':
    case 'outline_review':
    case 'style':
    case 'style_review':
    case 'writer':
    case 'designer':
    case 'validate':
    case 'repair':
    case 'coherence':
    case 'review_vlm':
    case 'export':
    case 'artifact':
      return value
    case 'slides':
      return 'writer'
    case 'design_slides':
      return 'designer'
    case 'review':
      return 'review_vlm'
    case 'render':
    case 'export_progress':
      return 'export'
    case 'done':
      return 'artifact'
    default:
      return ''
  }
}

function latestProgressStep(message: StatusMessage) {
  return [...message.steps].reverse().find((step) => step.stage && step.stage !== 'error') ?? null
}

function currentProgressLabel(message: StatusMessage) {
  const latest = latestProgressStep(message)
  if (!latest) {
    return message.text
  }
  return latest.message || message.text
}

function currentProgressStageLabel(message: StatusMessage) {
  const latest = latestProgressStep(message)
  if (!latest?.stage) {
    return message.state === 'waiting' ? '等待下一步' : '准备开始'
  }
  return pipelineStages.find((stage) => stage.key === latest.stage)?.label ?? latest.label
}

function progressChecklist(message: StatusMessage): PipelineStageView[] {
  const latest = latestProgressStep(message)
  const latestKey = latest?.stage ?? ''
  const latestIndex = pipelineStages.findIndex((stage) => stage.key === latestKey)
  const failed = message.state === 'error'

  return pipelineStages.map((stage, index) => {
    let status: PipelineStageStatus = 'pending'
    if (message.state === 'done') {
      status = 'done'
    } else if (latestIndex >= 0 && index < latestIndex) {
      status = 'done'
    } else if (latestIndex >= 0 && index === latestIndex) {
      status = failed ? 'error' : message.state === 'waiting' ? 'waiting' : 'running'
    }
    return {
      ...stage,
      status,
    }
  })
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
    case 'cancelled':
      return '已取消'
    default:
      return ''
  }
}

function slideStatusLabel(status: SlideProgressStatus) {
  switch (status) {
    case 'running':
      return 'Generating'
    case 'ready':
      return 'Ready'
    case 'reviewed':
      return 'Checked'
    case 'warning':
      return 'Review'
    case 'error':
      return 'Error'
    default:
      return 'Queued'
  }
}

function slideStatusPillClass(status: SlideProgressStatus) {
  switch (status) {
    case 'running':
      return 'is-running'
    case 'ready':
    case 'reviewed':
      return 'is-done'
    case 'warning':
      return 'is-waiting'
    case 'error':
      return 'is-error'
    default:
      return 'is-waiting'
  }
}

function artifactAssetUrl(requestId: string | undefined, relativePath: string | undefined) {
  if (!requestId || !relativePath) {
    return ''
  }
  return `/api/ppt-agent/artifacts/${encodeURIComponent(requestId)}/${relativePath
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/')}`
}

function previewSrc(requestId: string | undefined, thumbB64: string, pngPath: string) {
  const thumbUrl = thumbB64 ? `data:image/webp;base64,${thumbB64}` : ''
  const normalizedPngPath = pngPath.trim()

  if (!normalizedPngPath) {
    return thumbUrl
  }

  if (/^(data:|blob:|https?:\/\/)/i.test(normalizedPngPath)) {
    return normalizedPngPath
  }

  const cacheKey = assetCacheKey(requestId, normalizedPngPath, versionFromAssetPath(normalizedPngPath))
  if (previewObjectUrls[cacheKey]) {
    return previewObjectUrls[cacheKey]
  }

  if (requestId && !previewFailures.has(cacheKey)) {
    void loadPreviewAsset(cacheKey, requestId, normalizedPngPath)
  }

  return thumbUrl
}

function versionFromAssetPath(path: string) {
  const match = path.match(/_v(\d+)\.[^.]+$/i)
  return match ? Number(match[1]) : 1
}

async function loadPreviewAsset(cacheKey: string, requestId: string, relativePath: string) {
  if (previewLoads.has(cacheKey)) {
    return
  }

  const session = readSessionFromStorage()
  if (!session?.access_token) {
    return
  }

  previewLoads.add(cacheKey)
  try {
    const response = await fetch(artifactAssetUrl(requestId, relativePath), {
      headers: {
        Authorization: `Bearer ${session.access_token}`,
      },
    })
    if (!response.ok) {
      previewFailures.add(cacheKey)
      return
    }
    const blob = await response.blob()
    previewObjectUrls[cacheKey] = URL.createObjectURL(blob)
  } catch {
    previewFailures.add(cacheKey)
  } finally {
    previewLoads.delete(cacheKey)
  }
}

function messageRequestId(message: ChatMessage) {
  return 'requestId' in message ? message.requestId : undefined
}

function openPreview(src: string, title: string, subtitle: string) {
  if (!src) {
    return
  }
  previewLightbox.value = { src, title, subtitle }
}

function closePreview() {
  previewLightbox.value = null
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
                  ? '继续查看任务；整套修改请新建对话，单页修改请使用页面卡片中的重做入口。'
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
                <div v-if="message.fileNames?.length" class="file-chip-list">
                  <div v-for="fileName in message.fileNames" :key="fileName" class="file-chip readonly">
                    <Paperclip :size="14" />
                    <span>{{ fileName }}</span>
                  </div>
                </div>
              </template>

              <template v-else-if="message.kind === 'status'">
                <div class="status-header">
                  <strong>{{ message.title }}</strong>
                  <span :class="['status-pill', `is-${message.state}`]">{{ statusStateLabel(message.state) }}</span>
                </div>
                <p>{{ message.text }}</p>

                <div v-if="message.steps.length || message.state === 'running'" class="active-progress-card">
                  <div class="active-progress-icon" :class="`is-${message.state}`">
                    <Loader2 v-if="message.state === 'running'" class="spin" :size="18" />
                    <AlertCircle v-else-if="message.state === 'error'" :size="18" />
                    <CheckCircle2 v-else :size="18" />
                  </div>
                  <div>
                    <span>当前步骤</span>
                    <strong>{{ currentProgressStageLabel(message) }}</strong>
                    <p>{{ currentProgressLabel(message) }}</p>
                  </div>
                </div>

                <ol v-if="message.steps.length || message.state !== 'waiting'" class="status-checklist">
                  <li
                    v-for="item in progressChecklist(message)"
                    :key="item.key"
                    :class="`is-${item.status}`"
                  >
                    <span class="checklist-marker">
                      <Loader2 v-if="item.status === 'running'" class="spin" :size="15" />
                      <CheckCircle2 v-else-if="item.status === 'done'" :size="15" />
                      <AlertCircle v-else-if="item.status === 'error'" :size="15" />
                    </span>
                    <div>
                      <strong>{{ item.label }}</strong>
                      <p>{{ item.description }}</p>
                    </div>
                  </li>
                </ol>

                <div v-if="message.slides.length" class="slide-progress-list">
                  <article
                    v-for="slide in message.slides"
                    :key="slide.index"
                    :class="['slide-progress-card', `is-${slide.status}`]"
                  >
                    <div class="slide-progress-head">
                      <span class="slide-number">{{ slide.index }}</span>
                      <div>
                        <strong>{{ slide.title }}</strong>
                        <p v-if="slide.keyMessage">{{ slide.keyMessage }}</p>
                      </div>
                      <span :class="['status-pill', slideStatusPillClass(slide.status)]">
                        {{ slideStatusLabel(slide.status) }}
                      </span>
                    </div>

                    <button
                      v-if="slide.previewThumb || slide.previewPng"
                      class="slide-preview-frame"
                      type="button"
                      @click="
                        openPreview(
                          previewSrc(messageRequestId(message), slide.previewThumb, slide.previewPng),
                          `Slide ${slide.index}`,
                          slide.title,
                        )
                      "
                    >
                      <img
                        v-if="previewSrc(messageRequestId(message), slide.previewThumb, slide.previewPng)"
                        class="slide-preview-thumb"
                        :src="previewSrc(messageRequestId(message), slide.previewThumb, slide.previewPng)"
                        :alt="`Slide ${slide.index} preview`"
                        loading="lazy"
                      />
                      <span v-else class="slide-preview-placeholder">Loading preview</span>
                      <span class="slide-preview-action">
                        <Maximize2 :size="14" />
                        Full preview
                      </span>
                    </button>

                    <ul v-if="slide.bullets.length" class="slide-bullets">
                      <li v-for="bullet in slide.bullets" :key="bullet">{{ bullet }}</li>
                    </ul>

                    <p v-if="slide.visualSuggestion" class="slide-visual">
                      <strong>Visual</strong>
                      {{ slide.visualSuggestion }}
                    </p>

                    <ul v-if="slide.warnings.length" class="slide-warnings">
                      <li v-for="warning in slide.warnings" :key="warning">{{ warning }}</li>
                    </ul>

                    <div v-if="message.state === 'done' || message.state === 'waiting'" class="outline-actions">
                      <input
                        v-model="slideFeedback[slide.index]"
                        class="outline-editor"
                        type="text"
                        :placeholder="`第 ${slide.index} 页修改意见，例如：减少文字，改成左右对比`"
                        :disabled="running"
                      />
                      <button class="btn ghost" type="button" :disabled="running" @click="regenerateSlide(slide)">
                        重做此页
                      </button>
                    </div>
                  </article>
                </div>

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
                  <button class="btn ghost" type="button" :disabled="running" @click="regenerateOutline(message)">
                    重新生成大纲
                  </button>
                </div>
              </template>

              <template v-else-if="message.kind === 'style'">
                <div class="status-header">
                  <strong>{{ message.title }}</strong>
                  <span :class="['status-pill', message.awaitingConfirm ? 'is-waiting' : 'is-done']">
                    {{ message.awaitingConfirm ? '待选择' : '已选择' }}
                  </span>
                </div>
                <p>{{ message.text }}</p>

                <textarea
                  v-if="message.awaitingConfirm"
                  v-model="styleFeedback[message.id]"
                  class="outline-editor"
                  rows="2"
                  placeholder="可选：补充风格调整意见，例如更克制、减少装饰、提高数据对比度"
                />

                <div class="style-options">
                  <article v-for="option in message.options" :key="option.styleId" class="style-option-card">
                    <button
                      v-if="option.thumbB64 || option.coverPng"
                      class="style-preview-frame"
                      type="button"
                      @click="
                        openPreview(
                          previewSrc(messageRequestId(message), option.thumbB64, option.coverPng),
                          option.name,
                          'Style preview',
                        )
                      "
                    >
                      <img
                        v-if="previewSrc(messageRequestId(message), option.thumbB64, option.coverPng)"
                        :src="previewSrc(messageRequestId(message), option.thumbB64, option.coverPng)"
                        :alt="option.name"
                        loading="lazy"
                      />
                      <span v-else class="slide-preview-placeholder">Loading preview</span>
                      <span class="slide-preview-action">
                        <Maximize2 :size="14" />
                        Full preview
                      </span>
                    </button>
                    <div>
                      <strong>{{ option.name }}</strong>
                      <p>{{ option.tokensSummary }}</p>
                    </div>
                    <button
                      v-if="message.awaitingConfirm"
                      class="btn primary"
                      type="button"
                      :disabled="running"
                      @click="chooseStyle(message, option)"
                    >
                      <CheckCircle2 :size="18" />
                      选择
                    </button>
                  </article>
                </div>
              </template>

              <template v-else-if="message.kind === 'result'">
                <div class="status-header">
                  <strong>{{ message.title }}</strong>
                  <span :class="['status-pill', message.qualityStatus === 'needs_review' ? 'is-waiting' : 'is-done']">
                    {{ message.qualityStatus === 'needs_review' ? '需要复核' : message.degraded ? '完成但有降级' : '已完成' }}
                  </span>
                </div>
                <p>{{ message.text }}</p>

                <ul v-if="message.warnings.length" class="slide-warnings">
                  <li v-for="warning in message.warnings" :key="warning">{{ warning }}</li>
                </ul>
                <p v-if="message.degraded" class="form-error">导出已降级：部分页面文字不可编辑。</p>

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

              <div v-if="composer.files.length" class="file-chip-list">
                <div v-for="(file, index) in composer.files" :key="`${file.name}-${index}`" class="file-chip">
                  <div class="file-chip-main">
                    <Paperclip :size="14" />
                    <span>{{ file.name }}</span>
                  </div>
                  <button class="chip-close" type="button" @click="removeSelectedFile(index)">
                    <X :size="14" />
                  </button>
                </div>
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
                    multiple
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

                <label class="config-picker">
                  <span>生成模式</span>
                  <select v-model="generationMode" :disabled="running">
                    <option value="step_by_step">逐步确认</option>
                    <option value="quick">快速生成</option>
                  </select>
                </label>
              </div>

              <div class="composer-footnote">
                <span class="chat-note">支持 PDF、DOCX、Markdown、TXT 等文件。</span>
                <span v-if="uploadState" class="chat-note">{{ uploadState }}</span>
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

    <div v-if="previewLightbox" class="preview-lightbox" role="dialog" aria-modal="true" @click.self="closePreview">
      <div class="preview-lightbox-panel">
        <div class="preview-lightbox-head">
          <div>
            <span>{{ previewLightbox.subtitle }}</span>
            <strong>{{ previewLightbox.title }}</strong>
          </div>
          <button class="icon-button" type="button" aria-label="Close preview" @click="closePreview">
            <X :size="18" />
          </button>
        </div>
        <div class="preview-lightbox-stage">
          <img :src="previewLightbox.src" :alt="previewLightbox.title" />
        </div>
      </div>
    </div>
  </section>
</template>
