<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import AppShell from '@/components/AppShell.vue'
import {
  previewAgentConfigs,
  previewAgents,
  previewLogs,
  previewModels,
  previewProviders,
} from '@/dev/previewData'
import type { PresentationApi } from '@/features/presentations/api'
import AgentListPage from '@/pages/AgentListPage.vue'
import DashboardPage from '@/pages/DashboardPage.vue'
import LoginPage from '@/pages/LoginPage.vue'
import ModelConfigPage from '@/pages/ModelConfigPage.vue'
import PptAgentPage from '@/pages/PptAgentPage.vue'
import RequestLogsPage from '@/pages/RequestLogsPage.vue'
import { useWorkspaceStore } from '@/stores/workspace'
import type { AgentConfigPayload, ModelConfigPayload } from '@/types'

const store = useWorkspaceStore()
const previewParam = new URLSearchParams(window.location.search).get('preview')
const previewPages = new Set(['dashboard', 'models', 'agents', 'ppt', 'logs'])
const previewPage = import.meta.env.DEV && previewParam && previewPages.has(previewParam) ? previewParam : null
const isDesignPreview = previewPage !== null
const activePage = ref(previewPage ?? 'dashboard')
const saving = ref(false)
const designPreviewPresentationApi: PresentationApi = {
  createTask: async () => { throw new Error('设计预览中不能创建真实任务。') },
  getTask: async () => { throw new Error('设计预览中没有真实任务。') },
  subscribeTask: async () => {},
  cancelTask: async () => { throw new Error('设计预览中不能取消任务。') },
  downloadTaskFile: async () => { throw new Error('设计预览中没有可下载的文件。') },
}

interface FormActionCallbacks {
  onError: (message: string) => void
  onSuccess?: (message?: string) => void
}

const userLabel = computed(() => store.me.value?.username || (isDesignPreview ? 'Admin' : '当前用户'))
const pageProviders = computed(() => isDesignPreview ? previewProviders : store.providers.value)
const pageModels = computed(() => isDesignPreview ? previewModels : store.modelConfigs.value)
const pageAgents = computed(() => isDesignPreview ? previewAgents : store.agents.value)
const pageAgentConfigs = computed(() => isDesignPreview ? previewAgentConfigs : store.agentConfigs.value)
const pagePptConfigs = computed(() => pageAgentConfigs.value.filter((item) => item.agent_key === 'ppt_agent' && item.status === 'active'))
const pageLogs = computed(() => isDesignPreview ? previewLogs : store.logs.value)

onMounted(() => {
  if (!isDesignPreview) void store.boot()
})

async function login(username: string, password: string) {
  try {
    await store.login(username, password)
  } catch (error) {
    store.notify(normalizeError(error), 'error')
  }
}

async function register(username: string, password: string) {
  try {
    await store.api.register(username, password)
    store.notify('注册成功，可以直接登录。', 'success')
  } catch (error) {
    store.notify(normalizeError(error), 'error')
  }
}

async function saveModelConfig(payload: ModelConfigPayload, id: number | undefined, callbacks: FormActionCallbacks) {
  saving.value = true
  try {
    if (id) {
      const modelConfig = await store.api.updateModelConfig(id, payload)
      store.upsertModelConfig(modelConfig)
      store.notify('Model Config 已更新。', 'success')
    } else {
      if (!payload.api_key) {
        throw new Error('新建 Model Config 必须填写 API Key。')
      }
      const modelConfig = await store.api.createModelConfig(payload)
      store.upsertModelConfig(modelConfig)
      store.notify('Model Config 已创建。', 'success')
    }
    callbacks.onSuccess?.()
  } catch (error) {
    callbacks.onError(normalizeError(error))
  } finally {
    saving.value = false
  }
}

async function deleteModelConfig(id: number, callbacks: FormActionCallbacks) {
  if (!window.confirm('确认删除这个 Model Config？')) {
    return
  }
  try {
    await store.api.deleteModelConfig(id)
    store.removeModelConfig(id)
    store.notify('Model Config 已删除。', 'success')
    callbacks.onSuccess?.()
  } catch (error) {
    callbacks.onError(normalizeError(error))
  }
}

async function testCredential(
  provider: string,
  apiKey: string,
  baseUrl: string | undefined,
  callbacks: FormActionCallbacks,
) {
  try {
    const result = await store.api.testCredential({ provider, api_key: apiKey, base_url: baseUrl })
    callbacks.onSuccess?.(result.message || `凭证测试结果：${result.status}`)
  } catch (error) {
    callbacks.onError(normalizeError(error))
  }
}

async function saveAgentConfig(
  payload: AgentConfigPayload & { agent_id?: number },
  id: number | undefined,
  callbacks: FormActionCallbacks,
) {
  saving.value = true
  try {
    if (id) {
      const { agent_id: _agentId, ...updatePayload } = payload
      const agentConfig = await store.api.updateAgentConfig(id, updatePayload)
      store.upsertAgentConfig(agentConfig)
      store.notify('Agent Config 已更新。', 'success')
    } else {
      if (!payload.agent_id) {
        throw new Error('请选择 Agent。')
      }
      const agentConfig = await store.api.createAgentConfig(payload as AgentConfigPayload & { agent_id: number })
      store.upsertAgentConfig(agentConfig)
      store.notify('Agent Config 已创建。', 'success')
    }
    callbacks.onSuccess?.()
  } catch (error) {
    callbacks.onError(normalizeError(error))
  } finally {
    saving.value = false
  }
}

async function deleteAgentConfig(id: number, callbacks: FormActionCallbacks) {
  if (!window.confirm('确认删除这个 Agent Config？')) {
    return
  }
  try {
    await store.api.deleteAgentConfig(id)
    store.removeAgentConfig(id)
    store.notify('Agent Config 已删除。', 'success')
    callbacks.onSuccess?.()
  } catch (error) {
    callbacks.onError(normalizeError(error))
  }
}

function normalizeError(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}
</script>

<template>
  <LoginPage
    v-if="!store.isAuthenticated.value && !isDesignPreview"
    :busy="store.loading.auth"
    @login="login"
    @register="register"
  />

  <AppShell
    v-else
    :active-page="activePage"
    :user-label="userLabel"
    :api-health="isDesignPreview ? 'ok' : store.health.value"
    :loading="store.loading.data"
    @navigate="activePage = $event"
    @reload="store.reload"
    @logout="store.logout"
  >
    <div v-if="store.banner.value" class="banner" :class="`is-${store.banner.value.tone}`">
      {{ store.banner.value.text }}
    </div>

    <KeepAlive>
      <DashboardPage
        v-if="activePage === 'dashboard'"
        :models="pageModels"
        :agents="pageAgents"
        :agent-configs="pageAgentConfigs"
        :ppt-configs="pagePptConfigs"
        :api-health="isDesignPreview ? 'ok' : store.health.value"
        :preview="isDesignPreview"
        @navigate="activePage = $event"
      />
    </KeepAlive>

    <KeepAlive>
      <ModelConfigPage
        v-if="activePage === 'models'"
        :providers="pageProviders"
        :models="pageModels"
        :saving="saving"
        @save="saveModelConfig"
        @delete="deleteModelConfig"
        @test-credential="testCredential"
      />
    </KeepAlive>

    <KeepAlive>
      <AgentListPage
        v-if="activePage === 'agents'"
        :agents="pageAgents"
        :models="pageModels"
        :agent-configs="pageAgentConfigs"
        :saving="saving"
        @save="saveAgentConfig"
        @delete="deleteAgentConfig"
        @go-ppt="activePage = 'ppt'"
      />
    </KeepAlive>

    <KeepAlive>
    <PptAgentPage
      v-if="activePage === 'ppt'"
      :configs="pagePptConfigs"
      :api="isDesignPreview ? designPreviewPresentationApi : store.api.presentations"
    />
    </KeepAlive>

    <KeepAlive>
      <RequestLogsPage v-if="activePage === 'logs'" :logs="pageLogs" />
    </KeepAlive>
  </AppShell>
</template>
