import { computed, reactive, ref } from 'vue'

import { createApiClient, toSession } from '@/api/client'
import type {
  AgentConfig,
  AgentDefinition,
  MeResponse,
  ModelConfig,
  Provider,
  RequestLogEntry,
  SessionState,
} from '@/types'

const SESSION_KEY = 'agent-task-platform-front:session'

export function useWorkspaceStore() {
  const session = ref<SessionState | null>(readSession())
  const me = ref<MeResponse | null>(null)
  const health = ref<'unknown' | 'ok' | 'down'>('unknown')
  const providers = ref<Provider[]>([])
  const modelConfigs = ref<ModelConfig[]>([])
  const agents = ref<AgentDefinition[]>([])
  const agentConfigs = ref<AgentConfig[]>([])
  const logs = ref<RequestLogEntry[]>([])
  const banner = ref<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null)
  const loading = reactive({
    boot: false,
    auth: false,
    data: false,
  })

  const api = createApiClient({
    getSession: () => session.value,
    setSession: setSession,
    onUnauthorized: () => {
      setSession(null)
      banner.value = { tone: 'error', text: '登录状态已失效，请重新登录。' }
    },
    onLog: (entry) => {
      logs.value = [
        { id: Date.now() + Math.floor(Math.random() * 1000), time: new Date().toLocaleTimeString(), ...entry },
        ...logs.value,
      ].slice(0, 60)
    },
  })

  const isAuthenticated = computed(() => Boolean(session.value?.access_token))
  const activeModelConfigs = computed(() => modelConfigs.value.filter((item) => item.status === 'active'))
  const pptAgent = computed(() => agents.value.find((item) => item.agent_key === 'ppt_agent') ?? null)
  const pptAgentConfigs = computed(() =>
    agentConfigs.value.filter((item) => item.agent_key === 'ppt_agent' && item.status === 'active'),
  )

  function setSession(next: SessionState | null) {
    session.value = next
    if (next) {
      localStorage.setItem(SESSION_KEY, JSON.stringify(next))
    } else {
      localStorage.removeItem(SESSION_KEY)
      me.value = null
      providers.value = []
      modelConfigs.value = []
      agents.value = []
      agentConfigs.value = []
    }
  }

  function notify(text: string, tone: 'success' | 'error' | 'info' = 'info') {
    banner.value = { text, tone }
  }

  function upsertModelConfig(config: ModelConfig) {
    const others = modelConfigs.value
      .filter((item) => item.id !== config.id)
      .map((item) => (config.is_default ? { ...item, is_default: false } : item))

    modelConfigs.value = [config, ...others].sort(compareConfigs)
  }

  function removeModelConfig(id: number) {
    modelConfigs.value = modelConfigs.value.filter((item) => item.id !== id)
  }

  function upsertAgentConfig(config: AgentConfig) {
    const others = agentConfigs.value
      .filter((item) => item.id !== config.id)
      .map((item) => (
        config.is_default && item.agent_id === config.agent_id ? { ...item, is_default: false } : item
      ))

    agentConfigs.value = [config, ...others].sort(compareConfigs)
  }

  function removeAgentConfig(id: number) {
    agentConfigs.value = agentConfigs.value.filter((item) => item.id !== id)
  }

  async function boot() {
    loading.boot = true
    try {
      try {
        await api.health()
        health.value = 'ok'
      } catch {
        health.value = 'down'
      }
      if (session.value?.access_token) {
        await reload()
      }
    } finally {
      loading.boot = false
    }
  }

  async function login(username: string, password: string) {
    loading.auth = true
    try {
      const auth = await api.login(username, password)
      setSession(toSession(auth))
      await reload()
      notify('登录成功，已进入工作台。', 'success')
    } finally {
      loading.auth = false
    }
  }

  async function logout() {
    try {
      if (session.value) {
        await api.logout()
      }
    } finally {
      setSession(null)
      notify('已退出登录。', 'success')
    }
  }

  async function reload() {
    loading.data = true
    try {
      const [userResult, providerResult, modelResult, agentResult, configResult] = await Promise.allSettled([
        api.me(),
        api.providers(),
        api.modelConfigs(),
        api.agents(),
        api.agentConfigs(),
      ])

      // Stop here if auth became invalid during the reload flow.
      if (!session.value?.access_token) {
        return
      }

      if (userResult.status === 'fulfilled') {
        me.value = userResult.value
      }
      if (providerResult.status === 'fulfilled') {
        providers.value = providerResult.value.providers
      }
      if (modelResult.status === 'fulfilled') {
        modelConfigs.value = modelResult.value
      }
      if (agentResult.status === 'fulfilled') {
        agents.value = agentResult.value
      }
      if (configResult.status === 'fulfilled') {
        agentConfigs.value = configResult.value
      }
    } finally {
      loading.data = false
    }
  }

  return {
    activeModelConfigs,
    agentConfigs,
    agents,
    api,
    banner,
    boot,
    health,
    isAuthenticated,
    loading,
    login,
    logs,
    logout,
    me,
    modelConfigs,
    notify,
    pptAgent,
    pptAgentConfigs,
    providers,
    removeAgentConfig,
    removeModelConfig,
    reload,
    session,
    upsertAgentConfig,
    upsertModelConfig,
  }
}

function compareConfigs<T extends { id: number; is_default: boolean; updated_at: string }>(left: T, right: T) {
  if (left.is_default !== right.is_default) {
    return left.is_default ? -1 : 1
  }
  const updatedAtDifference = Date.parse(right.updated_at) - Date.parse(left.updated_at)
  return updatedAtDifference || right.id - left.id
}

function readSession() {
  const raw = localStorage.getItem(SESSION_KEY)
  if (!raw) {
    return null
  }
  try {
    return JSON.parse(raw) as SessionState
  } catch {
    return null
  }
}
