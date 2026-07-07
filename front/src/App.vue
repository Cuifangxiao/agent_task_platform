<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import AppShell from '@/components/AppShell.vue'
import AgentListPage from '@/pages/AgentListPage.vue'
import DashboardPage from '@/pages/DashboardPage.vue'
import LoginPage from '@/pages/LoginPage.vue'
import ModelConfigPage from '@/pages/ModelConfigPage.vue'
import PptAgentPage from '@/pages/PptAgentPage.vue'
import RequestLogsPage from '@/pages/RequestLogsPage.vue'
import { useWorkspaceStore } from '@/stores/workspace'
import type { AgentConfigPayload, ModelConfigPayload } from '@/types'

const store = useWorkspaceStore()
const activePage = ref('dashboard')
const saving = ref(false)

const userLabel = computed(() => store.me.value?.username ? `当前用户：${store.me.value.username}` : '未获取用户信息')

onMounted(() => {
  void store.boot()
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

async function saveModelConfig(payload: ModelConfigPayload, id?: number) {
  saving.value = true
  try {
    if (id) {
      await store.api.updateModelConfig(id, payload)
      store.notify('Model Config 已更新。', 'success')
    } else {
      if (!payload.api_key) {
        throw new Error('新建 Model Config 必须填写 API Key。')
      }
      await store.api.createModelConfig(payload)
      store.notify('Model Config 已创建。', 'success')
    }
    await store.reload()
  } catch (error) {
    store.notify(normalizeError(error), 'error')
  } finally {
    saving.value = false
  }
}

async function deleteModelConfig(id: number) {
  if (!window.confirm('确认删除这个 Model Config？')) {
    return
  }
  try {
    await store.api.deleteModelConfig(id)
    await store.reload()
    store.notify('Model Config 已删除。', 'success')
  } catch (error) {
    store.notify(normalizeError(error), 'error')
  }
}

async function testCredential(provider: string, apiKey: string, baseUrl?: string) {
  try {
    const result = await store.api.testCredential({ provider, api_key: apiKey, base_url: baseUrl })
    store.notify(result.message || `凭证测试结果：${result.status}`, result.status === 'ok' ? 'success' : 'info')
  } catch (error) {
    store.notify(normalizeError(error), 'error')
  }
}

async function saveAgentConfig(payload: AgentConfigPayload & { agent_id?: number }, id?: number) {
  saving.value = true
  try {
    if (id) {
      const { agent_id: _agentId, ...updatePayload } = payload
      await store.api.updateAgentConfig(id, updatePayload)
      store.notify('Agent Config 已更新。', 'success')
    } else {
      if (!payload.agent_id) {
        throw new Error('请选择 Agent。')
      }
      await store.api.createAgentConfig(payload as AgentConfigPayload & { agent_id: number })
      store.notify('Agent Config 已创建。', 'success')
    }
    await store.reload()
  } catch (error) {
    store.notify(normalizeError(error), 'error')
  } finally {
    saving.value = false
  }
}

async function deleteAgentConfig(id: number) {
  if (!window.confirm('确认删除这个 Agent Config？')) {
    return
  }
  try {
    await store.api.deleteAgentConfig(id)
    await store.reload()
    store.notify('Agent Config 已删除。', 'success')
  } catch (error) {
    store.notify(normalizeError(error), 'error')
  }
}

function normalizeError(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}
</script>

<template>
  <LoginPage
    v-if="!store.isAuthenticated.value"
    :busy="store.loading.auth"
    @login="login"
    @register="register"
  />

  <AppShell
    v-else
    :active-page="activePage"
    :user-label="userLabel"
    :api-health="store.health.value"
    :loading="store.loading.data"
    @navigate="activePage = $event"
    @reload="store.reload"
    @logout="store.logout"
  >
    <div v-if="store.banner.value" class="banner" :class="`is-${store.banner.value.tone}`">
      {{ store.banner.value.text }}
    </div>

    <DashboardPage
      v-if="activePage === 'dashboard'"
      :models="store.modelConfigs.value"
      :agents="store.agents.value"
      :agent-configs="store.agentConfigs.value"
      :ppt-configs="store.pptAgentConfigs.value"
      @navigate="activePage = $event"
    />

    <ModelConfigPage
      v-else-if="activePage === 'models'"
      :providers="store.providers.value"
      :models="store.modelConfigs.value"
      :saving="saving"
      @save="saveModelConfig"
      @delete="deleteModelConfig"
      @test-credential="testCredential"
    />

    <AgentListPage
      v-else-if="activePage === 'agents'"
      :agents="store.agents.value"
      :models="store.modelConfigs.value"
      :agent-configs="store.agentConfigs.value"
      :saving="saving"
      @save="saveAgentConfig"
      @delete="deleteAgentConfig"
      @go-ppt="activePage = 'ppt'"
    />

    <PptAgentPage
      v-else-if="activePage === 'ppt'"
      :configs="store.pptAgentConfigs.value"
      :agents="store.agents.value"
      :models="store.modelConfigs.value"
      :stream-ppt="store.api.streamPpt"
      :resume-ppt="store.api.resumePpt"
    />

    <RequestLogsPage v-else :logs="store.logs.value" />
  </AppShell>
</template>
