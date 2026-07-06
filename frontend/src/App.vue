<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  Activity,
  BadgeCheck,
  Bot,
  Copy,
  Database,
  LogIn,
  LogOut,
  Plus,
  RefreshCw,
  Save,
  Send,
  ShieldCheck,
  Trash2,
  UserPlus,
  UserRound,
  Wrench,
  XCircle,
} from '@lucide/vue'

import { createApiClient } from './api'
import type {
  HealthResponse,
  MeResponse,
  ModelConfig,
  PptDeckChunk,
  Provider,
  ProviderCredentialTestResponse,
  ProviderParameter,
  RequestLogEntry,
  SessionState,
  StreamChunk,
} from './types'

type BannerTone = 'success' | 'error' | 'info'

interface BannerState {
  tone: BannerTone
  text: string
}

interface ModelConfigDraft {
  id: number | null
  name: string
  provider: string
  model_name: string
  base_url: string
  api_key: string
  provider_options: Record<string, unknown>
  temperature: number
  max_tokens: number
  is_default: boolean
  status: 'active' | 'inactive'
}

interface PptEventLog {
  id: number
  event: string
  stage: string
  message: string
  sequence: number
  payload?: unknown
}

const STORAGE_KEYS = {
  apiBase: 'agent-task-platform:api-base',
  session: 'agent-task-platform:session',
  selectedChatModel: 'agent-task-platform:selected-chat-model',
} as const

const apiBase = ref(readStoredString(STORAGE_KEYS.apiBase, '/api'))
const banner = ref<BannerState | null>(null)
const health = ref<HealthResponse | null>(null)
const currentUser = ref<MeResponse | null>(null)
const providers = ref<Provider[]>([])
const modelConfigs = ref<ModelConfig[]>([])
const selectedModelConfig = ref<ModelConfig | null>(null)
const credentialResult = ref<ProviderCredentialTestResponse | null>(null)
const activityLogs = ref<RequestLogEntry[]>([])
const session = ref<SessionState | null>(readStoredJson<SessionState>(STORAGE_KEYS.session))
const selectedChatModelConfigId = ref<number | null>(readStoredNumber(STORAGE_KEYS.selectedChatModel))

const registerForm = reactive({
  username: '',
  password: '',
})

const loginForm = reactive({
  username: 'admin',
  password: '123456',
  device: 'web-frontend',
})

const credentialForm = reactive({
  provider: '',
  api_key: '',
  base_url: '',
})

const modelConfigForm = reactive<ModelConfigDraft>(createEmptyModelConfigDraft())
const modelFormMode = ref<'create' | 'edit'>('create')

const chatForm = reactive({
  model_config_id: '',
  message: '你好，帮我根据这个后端接口梳理一个前端联调方案。',
  system_prompt: '你是一个简洁、可靠的助手。',
})

const pptForm = reactive({
  model_config_id: '',
  topic: 'Build a personal PPTAgent from the current agent platform',
  audience: 'solo developer',
  goal: 'explain the architecture, MVP scope, and next steps',
  page_count: 8,
  language: 'zh-CN',
  style: 'concise business deck',
  source_text: '',
})

const streamState = reactive({
  requestId: '',
  content: '',
  error: '',
  done: false,
  lastEvent: '',
})

const pptState = reactive({
  requestId: '',
  error: '',
  done: false,
  lastEvent: '',
  artifactUrl: '',
})

const pptEvents = ref<PptEventLog[]>([])
const chatAbortController = ref<AbortController | null>(null)
const pptAbortController = ref<AbortController | null>(null)

const busy = reactive({
  health: false,
  register: false,
  login: false,
  refresh: false,
  me: false,
  logout: false,
  providers: false,
  credential: false,
  modelConfigs: false,
  modelSave: false,
  detail: false,
  deleteId: 0,
  chat: false,
  ppt: false,
})

const isAuthenticated = computed(() => Boolean(session.value?.access_token))
const selectedProvider = computed(() => providers.value.find((item) => item.name === modelConfigForm.provider) ?? null)
const selectedProviderModels = computed(() => selectedProvider.value?.models ?? [])
const selectedProviderModel = computed(
  () =>
    selectedProviderModels.value.find(
      (item) => item.model_name === modelConfigForm.model_name || item.id === modelConfigForm.model_name,
    ) ?? null,
)
const providerSpecificParameters = computed(() =>
  (selectedProviderModel.value?.parameters ?? []).filter(
    (parameter) => parameter.name !== 'temperature' && parameter.name !== 'max_tokens',
  ),
)
const modelFormTitle = computed(() =>
  modelFormMode.value === 'create'
    ? '创建 Model Config'
    : `编辑 Model Config #${modelConfigForm.id ?? ''}`,
)
const selectedChatConfigLabel = computed(() => {
  if (selectedChatModelConfigId.value === null) {
    return '自动选择默认 active 配置'
  }
  const config = modelConfigs.value.find((item) => item.id === selectedChatModelConfigId.value)
  return config ? `${config.name} (#${config.id})` : `指定配置 #${selectedChatModelConfigId.value}`
})

const api = createApiClient({
  getApiBase: () => apiBase.value,
  getSession: () => session.value,
  setSession(nextSession) {
    session.value = nextSession
    if (!nextSession) {
      resetProtectedState()
    }
  },
  addLog(entry) {
    activityLogs.value = [
      {
        id: Date.now() + Math.floor(Math.random() * 1000),
        time: new Date().toLocaleTimeString(),
        ...entry,
      },
      ...activityLogs.value,
    ].slice(0, 40)
  },
})

watch(
  apiBase,
  (value) => {
    localStorage.setItem(STORAGE_KEYS.apiBase, value)
  },
  { immediate: true },
)

watch(
  session,
  (value) => {
    if (value) {
      localStorage.setItem(STORAGE_KEYS.session, JSON.stringify(value))
    } else {
      localStorage.removeItem(STORAGE_KEYS.session)
    }
  },
  { deep: true, immediate: true },
)

watch(
  selectedChatModelConfigId,
  (value) => {
    if (value === null) {
      localStorage.removeItem(STORAGE_KEYS.selectedChatModel)
      chatForm.model_config_id = ''
      return
    }

    localStorage.setItem(STORAGE_KEYS.selectedChatModel, String(value))
    chatForm.model_config_id = String(value)
  },
  { immediate: true },
)

onMounted(async () => {
  await runHealthCheck(true)
  if (isAuthenticated.value) {
    await reloadWorkspace(true)
  }
})

function createEmptyModelConfigDraft(): ModelConfigDraft {
  return {
    id: null,
    name: '',
    provider: '',
    model_name: '',
    base_url: '',
    api_key: '',
    provider_options: {},
    temperature: 1,
    max_tokens: 4096,
    is_default: false,
    status: 'active',
  }
}

function readStoredJson<T>(key: string) {
  const raw = localStorage.getItem(key)
  if (!raw) {
    return null
  }

  try {
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

function readStoredString(key: string, fallback: string) {
  return localStorage.getItem(key) ?? fallback
}

function readStoredNumber(key: string) {
  const raw = localStorage.getItem(key)
  if (!raw) {
    return null
  }
  const parsed = Number.parseInt(raw, 10)
  return Number.isFinite(parsed) ? parsed : null
}

function resetProtectedState() {
  currentUser.value = null
  providers.value = []
  modelConfigs.value = []
  selectedModelConfig.value = null
  credentialResult.value = null
  selectedChatModelConfigId.value = null
  resetModelConfigForm()
  stopChatStream()
  stopPptStream()
}

function resetModelConfigForm() {
  Object.assign(modelConfigForm, createEmptyModelConfigDraft())
  modelFormMode.value = 'create'

  if (providers.value.length > 0) {
    modelConfigForm.provider = providers.value[0].name
    onModelProviderChange()
  }
}

function setBanner(text: string, tone: BannerTone = 'info') {
  banner.value = { text, tone }
}

function clearBanner() {
  banner.value = null
}

function normalizeError(error: unknown) {
  if (error instanceof Error) {
    return error.message
  }
  return String(error)
}

async function runHealthCheck(silent = false) {
  busy.health = true
  try {
    health.value = await api.request<HealthResponse>('/healthz')
    if (!silent) {
      setBanner('后端健康检查通过。', 'success')
    }
  } catch (error) {
    if (!silent) {
      setBanner(normalizeError(error), 'error')
    }
  } finally {
    busy.health = false
  }
}

async function registerUser() {
  busy.register = true
  clearBanner()
  try {
    await api.request('/register', {
      method: 'POST',
      body: {
        username: registerForm.username,
        password: registerForm.password,
      },
    })
    setBanner('注册成功，现在可以直接登录。', 'success')
  } catch (error) {
    setBanner(normalizeError(error), 'error')
  } finally {
    busy.register = false
  }
}

async function loginUser() {
  busy.login = true
  clearBanner()
  try {
    const auth = await api.request<SessionState & { message: string }>('/login', {
      method: 'POST',
      body: {
        username: loginForm.username,
        password: loginForm.password,
        device: loginForm.device,
      },
    })

    session.value = {
      access_token: auth.access_token,
      refresh_token: auth.refresh_token,
      token_type: auth.token_type,
      session_id: auth.session_id,
      device: auth.device,
      access_expires_at: auth.access_expires_at,
      refresh_expires_at: auth.refresh_expires_at,
    }
    await reloadWorkspace(true)
    setBanner('登录成功，已加载用户信息和模型配置。', 'success')
  } catch (error) {
    setBanner(normalizeError(error), 'error')
  } finally {
    busy.login = false
  }
}

async function refreshSession() {
  if (!session.value?.refresh_token) {
    setBanner('当前没有可用的 refresh token。', 'error')
    return
  }

  busy.refresh = true
  clearBanner()
  try {
    const auth = await api.request<SessionState & { message: string }>('/refresh', {
      method: 'POST',
      body: {
        refresh_token: session.value.refresh_token,
      },
    })
    session.value = {
      access_token: auth.access_token,
      refresh_token: auth.refresh_token,
      token_type: auth.token_type,
      session_id: auth.session_id,
      device: auth.device,
      access_expires_at: auth.access_expires_at,
      refresh_expires_at: auth.refresh_expires_at,
    }
    setBanner('登录态已刷新。', 'success')
  } catch (error) {
    setBanner(normalizeError(error), 'error')
  } finally {
    busy.refresh = false
  }
}

async function loadMe(silent = false) {
  busy.me = true
  try {
    currentUser.value = await api.request<MeResponse>('/me', {
      auth: true,
    })
    if (!silent) {
      setBanner('已获取当前登录用户信息。', 'success')
    }
  } catch (error) {
    if (!silent) {
      setBanner(normalizeError(error), 'error')
    }
  } finally {
    busy.me = false
  }
}

async function logoutUser() {
  if (!isAuthenticated.value) {
    setBanner('当前没有登录会话。', 'info')
    return
  }

  busy.logout = true
  clearBanner()
  try {
    await api.request('/logout', {
      method: 'POST',
      auth: true,
    })
    session.value = null
    setBanner('已退出登录，并清空本地会话。', 'success')
  } catch (error) {
    setBanner(normalizeError(error), 'error')
  } finally {
    busy.logout = false
  }
}

async function loadProviders(silent = false) {
  busy.providers = true
  try {
    const response = await api.request<{ providers: Provider[] }>('/providers', {
      auth: true,
    })
    providers.value = response.providers
    ensureToolFormsUseLoadedProviders()
    if (!silent) {
      setBanner(`已加载 ${providers.value.length} 个 provider。`, 'success')
    }
  } catch (error) {
    if (!silent) {
      setBanner(normalizeError(error), 'error')
    }
  } finally {
    busy.providers = false
  }
}

async function loadModelConfigs(silent = false) {
  busy.modelConfigs = true
  try {
    modelConfigs.value = await api.request<ModelConfig[]>('/model-configs', {
      auth: true,
    })

    if (
      selectedChatModelConfigId.value !== null &&
      !modelConfigs.value.some((item) => item.id === selectedChatModelConfigId.value)
    ) {
      selectedChatModelConfigId.value = null
    }

    if (!silent) {
      setBanner(`已加载 ${modelConfigs.value.length} 条模型配置。`, 'success')
    }
  } catch (error) {
    if (!silent) {
      setBanner(normalizeError(error), 'error')
    }
  } finally {
    busy.modelConfigs = false
  }
}

async function loadModelConfigDetail(id: number, silent = false) {
  busy.detail = true
  try {
    selectedModelConfig.value = await api.request<ModelConfig>(`/model-configs/${id}`, {
      auth: true,
    })
    if (!silent) {
      setBanner(`已获取配置 #${id} 的详情。`, 'success')
    }
  } catch (error) {
    if (!silent) {
      setBanner(normalizeError(error), 'error')
    }
  } finally {
    busy.detail = false
  }
}

async function reloadWorkspace(silent = false) {
  const results = await Promise.allSettled([loadMe(true), loadProviders(true), loadModelConfigs(true)])
  const failed = results.filter((item) => item.status === 'rejected')

  if (!silent) {
    if (failed.length === 0) {
      setBanner('工作台数据已同步完成。', 'success')
    } else {
      setBanner('部分数据刷新失败，请看下方日志或重试。', 'error')
    }
  }
}

function ensureToolFormsUseLoadedProviders() {
  if (providers.value.length === 0) {
    return
  }

  if (!credentialForm.provider || !providers.value.some((item) => item.name === credentialForm.provider)) {
    credentialForm.provider = providers.value[0].name
    onCredentialProviderChange()
  }

  if (!modelConfigForm.provider || !providers.value.some((item) => item.name === modelConfigForm.provider)) {
    modelConfigForm.provider = providers.value[0].name
    onModelProviderChange()
  }
}

function onCredentialProviderChange() {
  const provider = providers.value.find((item) => item.name === credentialForm.provider)
  if (!provider) {
    return
  }

  credentialForm.base_url = provider.default_base_url
}

function onModelProviderChange() {
  const provider = selectedProvider.value
  if (!provider) {
    return
  }

  modelConfigForm.base_url = provider.default_base_url

  const fallbackModel = provider.models[0]
  if (!provider.models.some((item) => item.model_name === modelConfigForm.model_name)) {
    modelConfigForm.model_name = fallbackModel?.model_name ?? ''
  }

  syncModelSpecificParameters(false)
}

function onModelNameChange() {
  syncModelSpecificParameters(false)
}

function syncModelSpecificParameters(preserveExisting: boolean) {
  const model = selectedProviderModel.value
  if (!model) {
    modelConfigForm.provider_options = {}
    return
  }

  const nextOptions: Record<string, unknown> = {}

  for (const parameter of model.parameters) {
    if (parameter.name === 'temperature') {
      const existing = preserveExisting ? modelConfigForm.temperature : undefined
      modelConfigForm.temperature = Number(coerceParameterValue(parameter, existing))
      continue
    }
    if (parameter.name === 'max_tokens') {
      const existing = preserveExisting ? modelConfigForm.max_tokens : undefined
      modelConfigForm.max_tokens = Number(coerceParameterValue(parameter, existing))
      continue
    }

    const existing = preserveExisting ? modelConfigForm.provider_options[parameter.name] : undefined
    nextOptions[parameter.name] = coerceParameterValue(parameter, existing)
  }

  modelConfigForm.provider_options = nextOptions
}

function coerceParameterValue(parameter: ProviderParameter, rawValue: unknown) {
  const value = rawValue ?? parameter.default_value

  switch (parameter.type) {
    case 'boolean':
      return value === true || value === 'true'
    case 'integer': {
      const parsed = Number.parseInt(String(value), 10)
      if (Number.isFinite(parsed)) {
        return parsed
      }
      return Number.isFinite(parameter.min) ? Number(parameter.min) : 0
    }
    case 'number': {
      const parsed = Number(value)
      if (Number.isFinite(parsed)) {
        return parsed
      }
      return Number.isFinite(parameter.min) ? Number(parameter.min) : 0
    }
    default:
      return value === undefined || value === null ? '' : String(value)
  }
}

function updateProviderOption(parameter: ProviderParameter, event: Event) {
  const target = event.target as HTMLInputElement | HTMLSelectElement

  if (parameter.type === 'boolean' && target instanceof HTMLInputElement) {
    modelConfigForm.provider_options[parameter.name] = target.checked
    return
  }

  if (parameter.type === 'integer') {
    modelConfigForm.provider_options[parameter.name] = Number.parseInt(target.value, 10)
    return
  }

  if (parameter.type === 'number') {
    modelConfigForm.provider_options[parameter.name] = Number(target.value)
    return
  }

  modelConfigForm.provider_options[parameter.name] = target.value
}

function getProviderOptionValue(parameter: ProviderParameter) {
  const value = modelConfigForm.provider_options[parameter.name]
  if (parameter.type === 'boolean') {
    return Boolean(value)
  }
  return value ?? ''
}

function populateModelConfigForm(config: ModelConfig) {
  modelFormMode.value = 'edit'
  modelConfigForm.id = config.id
  modelConfigForm.name = config.name
  modelConfigForm.provider = config.provider
  modelConfigForm.model_name = config.model_name
  modelConfigForm.base_url = config.base_url
  modelConfigForm.api_key = ''
  modelConfigForm.provider_options = { ...config.provider_options }
  modelConfigForm.temperature = config.temperature
  modelConfigForm.max_tokens = config.max_tokens
  modelConfigForm.is_default = config.is_default
  modelConfigForm.status = config.status
  syncModelSpecificParameters(true)
  selectedModelConfig.value = config
  setBanner(`已将配置 #${config.id} 填入编辑表单。`, 'info')
}

function buildModelConfigPayload() {
  const payload: Record<string, unknown> = {
    name: modelConfigForm.name.trim(),
    provider: modelConfigForm.provider.trim(),
    model_name: modelConfigForm.model_name.trim(),
    base_url: modelConfigForm.base_url.trim(),
    provider_options: modelConfigForm.provider_options,
    temperature: Number(modelConfigForm.temperature),
    max_tokens: Number(modelConfigForm.max_tokens),
    is_default: modelConfigForm.is_default,
    status: modelConfigForm.status,
  }

  if (modelFormMode.value === 'create' || modelConfigForm.api_key.trim()) {
    payload.api_key = modelConfigForm.api_key.trim()
  }

  return payload
}

async function saveModelConfig() {
  if (modelFormMode.value === 'create' && !modelConfigForm.api_key.trim()) {
    setBanner('创建配置时必须填写 api_key。', 'error')
    return
  }

  busy.modelSave = true
  clearBanner()
  try {
    const payload = buildModelConfigPayload()
    const method = modelFormMode.value === 'create' ? 'POST' : 'PUT'
    const path =
      modelFormMode.value === 'create' ? '/model-configs' : `/model-configs/${modelConfigForm.id ?? ''}`

    const saved = await api.request<ModelConfig>(path, {
      method,
      auth: true,
      body: payload,
    })

    selectedModelConfig.value = saved
    if (selectedChatModelConfigId.value === null) {
      selectedChatModelConfigId.value = saved.id
    }
    await loadModelConfigs(true)
    populateModelConfigForm(saved)
    modelConfigForm.api_key = ''
    setBanner(modelFormMode.value === 'create' ? '模型配置已创建。' : '模型配置已更新。', 'success')
  } catch (error) {
    setBanner(normalizeError(error), 'error')
  } finally {
    busy.modelSave = false
  }
}

async function deleteModelConfig(config: ModelConfig) {
  if (!window.confirm(`确认删除配置 #${config.id} ${config.name} 吗？`)) {
    return
  }

  busy.deleteId = config.id
  clearBanner()
  try {
    await api.request(`/model-configs/${config.id}`, {
      method: 'DELETE',
      auth: true,
    })
    if (selectedModelConfig.value?.id === config.id) {
      selectedModelConfig.value = null
    }
    if (selectedChatModelConfigId.value === config.id) {
      selectedChatModelConfigId.value = null
    }
    if (modelConfigForm.id === config.id) {
      resetModelConfigForm()
    }
    await loadModelConfigs(true)
    setBanner(`配置 #${config.id} 已删除。`, 'success')
  } catch (error) {
    setBanner(normalizeError(error), 'error')
  } finally {
    busy.deleteId = 0
  }
}

async function runCredentialTest() {
  if (!credentialForm.provider.trim() || !credentialForm.api_key.trim()) {
    setBanner('请至少填写 provider 和 api_key。', 'error')
    return
  }

  busy.credential = true
  clearBanner()
  try {
    credentialResult.value = await api.request<ProviderCredentialTestResponse>('/provider-credentials/test', {
      method: 'POST',
      auth: true,
      body: {
        provider: credentialForm.provider.trim(),
        api_key: credentialForm.api_key.trim(),
        base_url: credentialForm.base_url.trim(),
      },
    })

    setBanner(
      credentialResult.value.status === 'ok'
        ? '凭证测试通过。'
        : `凭证测试返回 failed: ${credentialResult.value.message}`,
      credentialResult.value.status === 'ok' ? 'success' : 'error',
    )
  } catch (error) {
    setBanner(normalizeError(error), 'error')
  } finally {
    busy.credential = false
  }
}

function useConfigForChat(config: ModelConfig) {
  selectedChatModelConfigId.value = config.id
  chatForm.model_config_id = String(config.id)
  setBanner(`聊天面板已切换到配置 #${config.id}。`, 'info')
}

function onChatConfigChange() {
  const rawValue = chatForm.model_config_id.trim()
  selectedChatModelConfigId.value = rawValue ? Number.parseInt(rawValue, 10) : null
}

function resetStreamState() {
  streamState.requestId = ''
  streamState.content = ''
  streamState.error = ''
  streamState.done = false
  streamState.lastEvent = ''
}

async function sendChat() {
  if (!chatForm.message.trim()) {
    setBanner('请输入要发送的 message。', 'error')
    return
  }

  busy.chat = true
  clearBanner()
  resetStreamState()
  const controller = new AbortController()
  chatAbortController.value = controller

  const body: Record<string, unknown> = {
    message: chatForm.message.trim(),
    system_prompt: chatForm.system_prompt.trim(),
  }
  if (selectedChatModelConfigId.value !== null) {
    body.model_config_id = selectedChatModelConfigId.value
  }

  try {
    await api.stream('/agent/stream-text', {
      auth: true,
      body,
      signal: controller.signal,
      onEvent(event) {
        streamState.lastEvent = event.event

        if (typeof event.data !== 'object' || event.data === null) {
          return
        }

        const chunk = event.data as StreamChunk
        if (chunk.request_id) {
          streamState.requestId = chunk.request_id
        }
        if (event.event === 'delta' && chunk.delta) {
          streamState.content += chunk.delta
        }
        if (event.event === 'error') {
          streamState.error = chunk.error ?? 'stream error'
          streamState.done = Boolean(chunk.done)
        }
        if (event.event === 'done') {
          streamState.done = true
        }
      },
    })

    if (!streamState.error) {
      setBanner('流式输出已完成。', 'success')
    } else {
      setBanner(streamState.error, 'error')
    }
  } catch (error) {
    const message = normalizeError(error)
    if (error instanceof DOMException && error.name === 'AbortError') {
      setBanner('已手动停止当前流式请求。', 'info')
    } else {
      streamState.error = message
      setBanner(message, 'error')
    }
  } finally {
    busy.chat = false
    chatAbortController.value = null
  }
}

function stopChatStream() {
  chatAbortController.value?.abort()
  chatAbortController.value = null
  busy.chat = false
}

function resetPptState() {
  pptState.requestId = ''
  pptState.error = ''
  pptState.done = false
  pptState.lastEvent = ''
  pptState.artifactUrl = ''
  pptEvents.value = []
}

async function generatePpt() {
  if (!pptForm.topic.trim()) {
    setBanner('Please enter a PPT topic.', 'error')
    return
  }

  busy.ppt = true
  clearBanner()
  resetPptState()
  const controller = new AbortController()
  pptAbortController.value = controller

  const body: Record<string, unknown> = {
    topic: pptForm.topic.trim(),
    audience: pptForm.audience.trim(),
    goal: pptForm.goal.trim(),
    page_count: Number(pptForm.page_count),
    language: pptForm.language.trim(),
    style: pptForm.style.trim(),
    source_text: pptForm.source_text.trim(),
  }
  const modelConfigId = Number.parseInt(pptForm.model_config_id, 10)
  if (Number.isFinite(modelConfigId) && modelConfigId > 0) {
    body.model_config_id = modelConfigId
  }

  try {
    await api.stream('/ppt-agent/generate', {
      auth: true,
      body,
      signal: controller.signal,
      onEvent(event) {
        pptState.lastEvent = event.event

        if (typeof event.data !== 'object' || event.data === null) {
          return
        }

        const chunk = event.data as PptDeckChunk
        if (chunk.request_id) {
          pptState.requestId = chunk.request_id
        }
        if (chunk.artifact_url) {
          pptState.artifactUrl = buildApiHref(chunk.artifact_url)
        }
        if (event.event === 'error') {
          pptState.error = chunk.error ?? 'PPT stream error'
          pptState.done = Boolean(chunk.done)
        }
        if (event.event === 'done') {
          pptState.done = true
        }

        pptEvents.value.push({
          id: Date.now() + pptEvents.value.length,
          event: event.event,
          stage: chunk.stage ?? '',
          message: chunk.message ?? '',
          sequence: chunk.sequence ?? pptEvents.value.length + 1,
          payload: chunk.payload,
        })
      },
    })

    if (!pptState.error) {
      setBanner('PPTAgent generation complete.', 'success')
    } else {
      setBanner(pptState.error, 'error')
    }
  } catch (error) {
    const message = normalizeError(error)
    if (error instanceof DOMException && error.name === 'AbortError') {
      setBanner('PPTAgent stream stopped.', 'info')
    } else {
      pptState.error = message
      setBanner(message, 'error')
    }
  } finally {
    busy.ppt = false
    pptAbortController.value = null
  }
}

function stopPptStream() {
  pptAbortController.value?.abort()
  pptAbortController.value = null
  busy.ppt = false
}

function buildApiHref(path: string) {
  const base = apiBase.value.trim()
  const normalizedPath = path.startsWith('/') ? path : `/${path}`

  if (!base || base === '/') {
    return normalizedPath
  }
  if (/^https?:\/\//i.test(base)) {
    return `${base.replace(/\/+$/, '')}${normalizedPath}`
  }

  const relativeBase = base.startsWith('/') ? base : `/${base}`
  return `${relativeBase.replace(/\/+$/, '')}${normalizedPath}`.replace(/\/{2,}/g, '/')
}

async function copyText(value: string, successText: string) {
  if (!value.trim()) {
    setBanner('没有可复制的内容。', 'info')
    return
  }

  try {
    await navigator.clipboard.writeText(value)
    setBanner(successText, 'success')
  } catch {
    setBanner('复制失败，请检查浏览器权限。', 'error')
  }
}

function formatDate(value: string) {
  if (!value) {
    return '-'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  return date.toLocaleString()
}

function toPrettyJson(value: unknown) {
  if (value === null || value === undefined || value === '') {
    return '暂无数据'
  }
  if (typeof value === 'string') {
    return value
  }
  return JSON.stringify(value, null, 2)
}

function pickLogTone(status: string | number) {
  if (status === 'stream') {
    return 'success'
  }
  if (status === 'aborted') {
    return 'info'
  }
  if (typeof status === 'number') {
    if (status >= 200 && status < 300) {
      return 'success'
    }
    if (status >= 400) {
      return 'error'
    }
  }
  return 'info'
}

function clearLogs() {
  activityLogs.value = []
}
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <div class="title-wrap">
        <p class="eyebrow">Vue 联调测试台</p>
        <h1>Agent Task Platform</h1>
        <p class="subtitle">
          我们按接口文档把认证、Provider、Model Config、凭证测试和 SSE 聊天都收在一个面板里，适合本地联调时快速来回试。
        </p>
      </div>

      <div class="toolbar">
        <label class="field compact">
          <span>API Base</span>
          <input v-model="apiBase" type="text" placeholder="/api" />
        </label>

        <div class="button-row">
          <button class="button secondary" :disabled="busy.health" @click="runHealthCheck()">
            <Activity class="icon" />
            <span>{{ busy.health ? '检查中' : '健康检查' }}</span>
          </button>
          <button class="button secondary" :disabled="!isAuthenticated" @click="reloadWorkspace()">
            <RefreshCw class="icon" />
            <span>刷新工作台</span>
          </button>
        </div>
      </div>
    </header>

    <section class="status-band">
      <div class="stat">
        <span class="stat-label">登录态</span>
        <strong>{{ isAuthenticated ? '已登录' : '未登录' }}</strong>
      </div>
      <div class="stat">
        <span class="stat-label">当前用户</span>
        <strong>{{ currentUser?.username ?? '未获取' }}</strong>
      </div>
      <div class="stat">
        <span class="stat-label">Provider 数量</span>
        <strong>{{ providers.length }}</strong>
      </div>
      <div class="stat">
        <span class="stat-label">Model Config 数量</span>
        <strong>{{ modelConfigs.length }}</strong>
      </div>
      <div class="stat wide">
        <span class="stat-label">聊天配置</span>
        <strong>{{ selectedChatConfigLabel }}</strong>
      </div>
    </section>

    <p v-if="banner" class="banner" :class="`is-${banner.tone}`">
      {{ banner.text }}
    </p>

    <main class="workspace">
      <div class="column">
        <section class="panel">
          <div class="section-heading">
            <div class="section-title">
              <ShieldCheck class="section-icon" />
              <div>
                <h2>认证与会话</h2>
                <p>注册、登录、刷新 token、查看当前用户和退出登录都在这里。</p>
              </div>
            </div>
          </div>

          <div class="auth-grid">
            <form class="form-block" @submit.prevent="registerUser">
              <div class="mini-heading">
                <UserPlus class="mini-icon" />
                <span>注册</span>
              </div>
              <label class="field">
                <span>用户名</span>
                <input v-model="registerForm.username" type="text" placeholder="new-user" />
              </label>
              <label class="field">
                <span>密码</span>
                <input v-model="registerForm.password" type="password" placeholder="至少 6 位" />
              </label>
              <button class="button primary" :disabled="busy.register">
                <UserPlus class="icon" />
                <span>{{ busy.register ? '提交中' : '注册账号' }}</span>
              </button>
            </form>

            <form class="form-block" @submit.prevent="loginUser">
              <div class="mini-heading">
                <LogIn class="mini-icon" />
                <span>登录</span>
              </div>
              <label class="field">
                <span>用户名</span>
                <input v-model="loginForm.username" type="text" placeholder="admin" />
              </label>
              <label class="field">
                <span>密码</span>
                <input v-model="loginForm.password" type="password" placeholder="123456" />
              </label>
              <label class="field">
                <span>设备标识</span>
                <input v-model="loginForm.device" type="text" placeholder="web-frontend" />
              </label>
              <div class="button-row">
                <button class="button primary" :disabled="busy.login">
                  <LogIn class="icon" />
                  <span>{{ busy.login ? '登录中' : '登录' }}</span>
                </button>
                <button type="button" class="button secondary" :disabled="busy.refresh" @click="refreshSession">
                  <RefreshCw class="icon" />
                  <span>{{ busy.refresh ? '刷新中' : '刷新会话' }}</span>
                </button>
              </div>
            </form>
          </div>

          <div class="session-card">
            <div class="mini-heading">
              <UserRound class="mini-icon" />
              <span>当前会话</span>
            </div>
            <div class="key-values">
              <div>
                <span>Access 过期</span>
                <strong>{{ session ? formatDate(session.access_expires_at) : '-' }}</strong>
              </div>
              <div>
                <span>Refresh 过期</span>
                <strong>{{ session ? formatDate(session.refresh_expires_at) : '-' }}</strong>
              </div>
              <div>
                <span>Session ID</span>
                <strong class="mono">{{ session?.session_id ?? '-' }}</strong>
              </div>
              <div>
                <span>Device</span>
                <strong>{{ session?.device ?? '-' }}</strong>
              </div>
            </div>

            <div class="button-row">
              <button class="button secondary" :disabled="!isAuthenticated || busy.me" @click="loadMe()">
                <BadgeCheck class="icon" />
                <span>{{ busy.me ? '获取中' : '获取 /me' }}</span>
              </button>
              <button
                class="button ghost"
                :disabled="!session?.access_token"
                @click="copyText(session?.access_token ?? '', 'access token 已复制。')"
              >
                <Copy class="icon" />
                <span>复制 Access Token</span>
              </button>
              <button class="button danger" :disabled="!isAuthenticated || busy.logout" @click="logoutUser">
                <LogOut class="icon" />
                <span>{{ busy.logout ? '退出中' : '退出登录' }}</span>
              </button>
            </div>

            <pre class="json-view">{{ toPrettyJson(currentUser) }}</pre>
          </div>
        </section>

        <section class="panel">
          <div class="section-heading">
            <div class="section-title">
              <Wrench class="section-icon" />
              <div>
                <h2>Provider 与凭证测试</h2>
                <p>先拿 provider 元数据，再直接对单个 provider 做探活。</p>
              </div>
            </div>
            <button class="button secondary" :disabled="!isAuthenticated || busy.providers" @click="loadProviders()">
              <Database class="icon" />
              <span>{{ busy.providers ? '加载中' : '拉取 Providers' }}</span>
            </button>
          </div>

          <form class="form-block" @submit.prevent="runCredentialTest">
            <div class="field-grid">
              <label class="field">
                <span>Provider</span>
                <select v-model="credentialForm.provider" @change="onCredentialProviderChange">
                  <option value="">请选择</option>
                  <option v-for="provider in providers" :key="provider.name" :value="provider.name">
                    {{ provider.display_name }} ({{ provider.name }})
                  </option>
                </select>
              </label>

              <label class="field">
                <span>Base URL</span>
                <input v-model="credentialForm.base_url" type="text" placeholder="https://api.example.com/v1" />
              </label>
            </div>

            <label class="field">
              <span>API Key</span>
              <input v-model="credentialForm.api_key" type="password" placeholder="sk-xxxxx" />
            </label>

            <div class="button-row">
              <button class="button primary" :disabled="!isAuthenticated || busy.credential">
                <BadgeCheck class="icon" />
                <span>{{ busy.credential ? '测试中' : '测试凭证' }}</span>
              </button>
            </div>
          </form>

          <pre class="json-view">{{ toPrettyJson(credentialResult) }}</pre>

          <div class="provider-list">
            <details v-for="provider in providers" :key="provider.name" class="provider-item">
              <summary>
                <div>
                  <strong>{{ provider.display_name }}</strong>
                  <span>{{ provider.name }}</span>
                </div>
                <small>{{ provider.models.length }} 个模型</small>
              </summary>
              <div class="provider-body">
                <p>{{ provider.description }}</p>
                <div class="meta-grid">
                  <span>默认 Base URL: {{ provider.default_base_url }}</span>
                  <span>SDK: {{ provider.sdk }}</span>
                </div>

                <table class="data-table compact">
                  <thead>
                    <tr>
                      <th>模型</th>
                      <th>参数</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="model in provider.models" :key="model.id">
                      <td>
                        <div class="stacked">
                          <strong>{{ model.display_name }}</strong>
                          <span class="mono">{{ model.model_name }}</span>
                        </div>
                      </td>
                      <td>
                        <div class="chips">
                          <span v-for="parameter in model.parameters" :key="parameter.name" class="chip">
                            {{ parameter.display_name }}
                          </span>
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </details>
          </div>
        </section>
      </div>

      <div class="column wide">
        <section class="panel">
          <div class="section-heading">
            <div class="section-title">
              <Database class="section-icon" />
              <div>
                <h2>{{ modelFormTitle }}</h2>
                <p>表单会按照 provider 和 model 参数动态补出 provider_options。</p>
              </div>
            </div>
            <div class="button-row">
              <button class="button secondary" :disabled="!isAuthenticated || busy.modelConfigs" @click="loadModelConfigs()">
                <RefreshCw class="icon" />
                <span>{{ busy.modelConfigs ? '加载中' : '刷新列表' }}</span>
              </button>
              <button class="button ghost" @click="resetModelConfigForm">
                <Plus class="icon" />
                <span>新建模式</span>
              </button>
            </div>
          </div>

          <form class="config-form" @submit.prevent="saveModelConfig">
            <div class="field-grid">
              <label class="field">
                <span>名称</span>
                <input v-model="modelConfigForm.name" type="text" placeholder="default deepseek" />
              </label>
              <label class="field">
                <span>Provider</span>
                <select v-model="modelConfigForm.provider" :disabled="providers.length === 0" @change="onModelProviderChange">
                  <option value="">请选择</option>
                  <option v-for="provider in providers" :key="provider.name" :value="provider.name">
                    {{ provider.display_name }} ({{ provider.name }})
                  </option>
                </select>
              </label>
              <label class="field">
                <span>Model</span>
                <select
                  v-model="modelConfigForm.model_name"
                  :disabled="selectedProviderModels.length === 0"
                  @change="onModelNameChange"
                >
                  <option value="">请选择</option>
                  <option v-for="model in selectedProviderModels" :key="model.id" :value="model.model_name">
                    {{ model.display_name }} ({{ model.model_name }})
                  </option>
                </select>
              </label>
              <label class="field">
                <span>Base URL</span>
                <input v-model="modelConfigForm.base_url" type="text" placeholder="https://api.example.com/v1" />
              </label>
              <label class="field">
                <span>API Key {{ modelFormMode === 'edit' ? '(留空则保留原值)' : '' }}</span>
                <input v-model="modelConfigForm.api_key" type="password" placeholder="sk-xxxxx" />
              </label>
              <label class="field">
                <span>状态</span>
                <select v-model="modelConfigForm.status">
                  <option value="active">active</option>
                  <option value="inactive">inactive</option>
                </select>
              </label>
              <label class="field">
                <span>Temperature</span>
                <input v-model.number="modelConfigForm.temperature" type="number" min="0" max="2" step="0.1" />
              </label>
              <label class="field">
                <span>Max Tokens</span>
                <input v-model.number="modelConfigForm.max_tokens" type="number" min="1" step="1" />
              </label>
              <label class="field checkbox">
                <input v-model="modelConfigForm.is_default" type="checkbox" />
                <span>设为默认配置</span>
              </label>
            </div>

            <div v-if="providerSpecificParameters.length > 0" class="parameter-block">
              <div class="mini-heading">
                <Wrench class="mini-icon" />
                <span>provider_options</span>
              </div>

              <div class="field-grid">
                <template v-for="parameter in providerSpecificParameters" :key="parameter.name">
                  <label v-if="parameter.type === 'enum'" class="field">
                    <span>{{ parameter.display_name }}</span>
                    <select :value="String(getProviderOptionValue(parameter))" @change="updateProviderOption(parameter, $event)">
                      <option v-for="option in parameter.options" :key="option" :value="option">{{ option }}</option>
                    </select>
                  </label>

                  <label v-else-if="parameter.type === 'boolean'" class="field checkbox">
                    <input
                      :checked="Boolean(getProviderOptionValue(parameter))"
                      type="checkbox"
                      @change="updateProviderOption(parameter, $event)"
                    />
                    <span>{{ parameter.display_name }}</span>
                  </label>

                  <label v-else class="field">
                    <span>{{ parameter.display_name }}</span>
                    <input
                      :value="String(getProviderOptionValue(parameter))"
                      :type="parameter.type === 'integer' || parameter.type === 'number' ? 'number' : 'text'"
                      :min="parameter.min"
                      :max="parameter.max"
                      :step="parameter.type === 'integer' ? 1 : parameter.type === 'number' ? '0.1' : undefined"
                      @input="updateProviderOption(parameter, $event)"
                    />
                  </label>
                </template>
              </div>
            </div>

            <div class="button-row">
              <button class="button primary" :disabled="!isAuthenticated || busy.modelSave">
                <Save class="icon" />
                <span>{{ busy.modelSave ? '保存中' : modelFormMode === 'create' ? '创建配置' : '保存修改' }}</span>
              </button>
            </div>
          </form>

          <div class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>名称</th>
                  <th>Provider / Model</th>
                  <th>状态</th>
                  <th>Key</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="config in modelConfigs" :key="config.id">
                  <td class="mono">#{{ config.id }}</td>
                  <td>
                    <div class="stacked">
                      <strong>{{ config.name }}</strong>
                      <span v-if="config.is_default" class="badge">default</span>
                    </div>
                  </td>
                  <td>
                    <div class="stacked">
                      <span>{{ config.provider }}</span>
                      <span class="mono">{{ config.model_name }}</span>
                    </div>
                  </td>
                  <td>
                    <span class="badge" :class="config.status === 'active' ? 'is-success' : 'is-muted'">
                      {{ config.status }}
                    </span>
                  </td>
                  <td class="mono">{{ config.api_key_masked || '-' }}</td>
                  <td>{{ formatDate(config.updated_at) }}</td>
                  <td>
                    <div class="table-actions">
                      <button class="icon-button" title="查看详情" @click="loadModelConfigDetail(config.id)">
                        <BadgeCheck class="icon" />
                      </button>
                      <button class="icon-button" title="填入编辑表单" @click="populateModelConfigForm(config)">
                        <Save class="icon" />
                      </button>
                      <button class="icon-button" title="用作聊天配置" @click="useConfigForChat(config)">
                        <Bot class="icon" />
                      </button>
                      <button
                        class="icon-button danger"
                        title="删除"
                        :disabled="busy.deleteId === config.id"
                        @click="deleteModelConfig(config)"
                      >
                        <Trash2 class="icon" />
                      </button>
                    </div>
                  </td>
                </tr>
                <tr v-if="modelConfigs.length === 0">
                  <td colspan="7" class="empty-row">当前还没有 model config。</td>
                </tr>
              </tbody>
            </table>
          </div>

          <pre class="json-view">{{ toPrettyJson(selectedModelConfig) }}</pre>
        </section>

        <section class="panel">
          <div class="section-heading">
            <div class="section-title">
              <Bot class="section-icon" />
              <div>
                <h2>Agent SSE 聊天</h2>
                <p>这里直接用 fetch 读取 POST + text/event-stream，按 delta 增量拼接内容。</p>
              </div>
            </div>
          </div>

          <form class="form-block" @submit.prevent="sendChat">
            <div class="field-grid">
              <label class="field">
                <span>使用的 Model Config</span>
                <select v-model="chatForm.model_config_id" @change="onChatConfigChange">
                  <option value="">自动选择默认 active 配置</option>
                  <option v-for="config in modelConfigs" :key="config.id" :value="String(config.id)">
                    #{{ config.id }} {{ config.name }}
                  </option>
                </select>
              </label>
              <label class="field">
                <span>Request ID</span>
                <input :value="streamState.requestId" type="text" readonly placeholder="发送后自动出现" />
              </label>
            </div>

            <label class="field">
              <span>Message</span>
              <textarea v-model="chatForm.message" rows="4" placeholder="请输入要发送给 Agent 的 message" />
            </label>

            <label class="field">
              <span>System Prompt</span>
              <textarea v-model="chatForm.system_prompt" rows="3" placeholder="可选系统提示词" />
            </label>

            <div class="button-row">
              <button class="button primary" :disabled="!isAuthenticated || busy.chat">
                <Send class="icon" />
                <span>{{ busy.chat ? '流式进行中' : '发送消息' }}</span>
              </button>
              <button type="button" class="button danger" :disabled="!busy.chat" @click="stopChatStream">
                <XCircle class="icon" />
                <span>停止流式请求</span>
              </button>
            </div>
          </form>

          <div class="chat-output">
            <div class="chat-meta">
              <span>最后事件：{{ streamState.lastEvent || '-' }}</span>
              <span>完成状态：{{ streamState.done ? 'done' : 'streaming / idle' }}</span>
            </div>
            <pre class="chat-text">{{ streamState.content || '这里会实时展示 delta 拼接后的文本。' }}</pre>
            <p v-if="streamState.error" class="inline-error">{{ streamState.error }}</p>
          </div>
        </section>

        <section class="panel">
          <div class="section-heading">
            <div class="section-title">
              <Bot class="section-icon" />
              <div>
                <h2>PPTAgent Deck</h2>
                <p>Generate a PPTX through Go SSE, Python gRPC, and a LangGraph deck workflow.</p>
              </div>
            </div>
          </div>

          <form class="form-block" @submit.prevent="generatePpt">
            <div class="field-grid">
              <label class="field">
                <span>Model Config</span>
                <select v-model="pptForm.model_config_id">
                  <option value="">Use default active config</option>
                  <option v-for="config in modelConfigs" :key="config.id" :value="String(config.id)">
                    #{{ config.id }} {{ config.name }}
                  </option>
                </select>
              </label>
              <label class="field">
                <span>Request ID</span>
                <input :value="pptState.requestId" type="text" readonly placeholder="Created after start" />
              </label>
              <label class="field">
                <span>Topic</span>
                <input v-model="pptForm.topic" type="text" placeholder="What should the deck explain?" />
              </label>
              <label class="field">
                <span>Audience</span>
                <input v-model="pptForm.audience" type="text" placeholder="Who will read it?" />
              </label>
              <label class="field">
                <span>Goal</span>
                <input v-model="pptForm.goal" type="text" placeholder="What should the deck achieve?" />
              </label>
              <label class="field">
                <span>Page Count</span>
                <input v-model.number="pptForm.page_count" type="number" min="1" max="30" step="1" />
              </label>
              <label class="field">
                <span>Language</span>
                <input v-model="pptForm.language" type="text" placeholder="zh-CN" />
              </label>
              <label class="field">
                <span>Style</span>
                <input v-model="pptForm.style" type="text" placeholder="concise business deck" />
              </label>
            </div>

            <label class="field">
              <span>Source Text</span>
              <textarea v-model="pptForm.source_text" rows="4" placeholder="Optional notes or source material" />
            </label>

            <div class="button-row">
              <button class="button primary" :disabled="!isAuthenticated || busy.ppt">
                <Send class="icon" />
                <span>{{ busy.ppt ? 'Generating' : 'Generate PPTX' }}</span>
              </button>
              <button type="button" class="button danger" :disabled="!busy.ppt" @click="stopPptStream">
                <XCircle class="icon" />
                <span>Stop</span>
              </button>
              <a v-if="pptState.artifactUrl" class="button secondary" :href="pptState.artifactUrl" target="_blank" rel="noreferrer">
                <Save class="icon" />
                <span>Download PPTX</span>
              </a>
            </div>
          </form>

          <div class="chat-output">
            <div class="chat-meta">
              <span>Last event: {{ pptState.lastEvent || '-' }}</span>
              <span>Status: {{ pptState.done ? 'done' : busy.ppt ? 'streaming' : 'idle' }}</span>
            </div>
            <div class="ppt-events">
              <div v-for="item in pptEvents" :key="item.id" class="ppt-event">
                <span class="badge">{{ item.sequence }}</span>
                <strong>{{ item.event }}</strong>
                <span>{{ item.stage }}</span>
                <span>{{ item.message }}</span>
              </div>
            </div>
            <pre class="json-view">{{ toPrettyJson(pptEvents[pptEvents.length - 1]?.payload ?? 'No PPT events yet.') }}</pre>
            <p v-if="pptState.error" class="inline-error">{{ pptState.error }}</p>
          </div>
        </section>

        <section class="panel">
          <div class="section-heading">
            <div class="section-title">
              <Activity class="section-icon" />
              <div>
                <h2>请求日志</h2>
                <p>每次请求和流式会话都会留一条记录，方便回看请求体和服务端返回。</p>
              </div>
            </div>
            <div class="button-row">
              <button class="button ghost" @click="clearLogs">
                <Trash2 class="icon" />
                <span>清空日志</span>
              </button>
            </div>
          </div>

          <div class="log-list">
            <details v-for="entry in activityLogs" :key="entry.id" class="log-item">
              <summary>
                <div class="log-summary">
                  <span class="badge" :class="`is-${pickLogTone(entry.status)}`">{{ entry.status }}</span>
                  <strong>{{ entry.method }} {{ entry.path }}</strong>
                  <span>{{ entry.time }}</span>
                </div>
                <small>{{ entry.note ?? '查看详情' }}</small>
              </summary>
              <div class="log-body">
                <div>
                  <h3>Request</h3>
                  <pre class="json-view">{{ toPrettyJson(entry.request) }}</pre>
                </div>
                <div>
                  <h3>Response</h3>
                  <pre class="json-view">{{ toPrettyJson(entry.response) }}</pre>
                </div>
              </div>
            </details>

            <p v-if="activityLogs.length === 0" class="empty-row">还没有请求日志，先点几个接口试试。</p>
          </div>
        </section>
      </div>
    </main>
  </div>
</template>
