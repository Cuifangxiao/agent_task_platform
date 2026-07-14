<script setup lang="ts">
import {
  ArrowRight,
  Bot,
  ChevronRight,
  CircleCheck,
  Clock3,
  Database,
  FileChartColumn,
  FileText,
  Layers3,
  Plus,
  Presentation,
} from '@lucide/vue'
import { computed } from 'vue'

import ProviderIcon from '@/components/ProviderIcon.vue'
import type { AgentConfig, AgentDefinition, ModelConfig } from '@/types'

const props = withDefaults(defineProps<{
  models: ModelConfig[]
  agents: AgentDefinition[]
  agentConfigs: AgentConfig[]
  pptConfigs: AgentConfig[]
  apiHealth: 'unknown' | 'ok' | 'down'
  preview?: boolean
}>(), {
  preview: false,
})

const emit = defineEmits<{
  navigate: [page: string]
}>()

interface DashboardTask {
  id: string | number
  title: string
  status: string
  tone: 'green' | 'blue' | 'amber' | 'red' | 'gray'
  updatedAt: string
}

const previewTasks: DashboardTask[] = [
  { id: 'preview-1', title: '2026 年半年度产品复盘', status: '已完成', tone: 'green', updatedAt: '2026-07-13 08:42' },
  { id: 'preview-2', title: 'AI Agent 平台方案', status: '生成中 (58%)', tone: 'blue', updatedAt: '2026-07-13 08:23' },
  { id: 'preview-3', title: '零售行业趋势分析', status: '排队中', tone: 'gray', updatedAt: '2026-07-13 07:55' },
]

const previewProviders = [
  { id: 'preview-openai', provider: 'openai', label: 'OpenAI (GPT-4o)' },
  { id: 'preview-deepseek', provider: 'deepseek', label: 'DeepSeek (V3)' },
  { id: 'preview-qwen', provider: 'qwen', label: 'Qwen (Qwen2.5)' },
]

const activeModels = computed(() => props.models.filter((item) => item.status === 'active'))
const activeAgents = computed(() => props.agents.filter((item) => item.status === 'active'))
const activeAgentConfigs = computed(() => props.agentConfigs.filter((item) => item.status === 'active'))
const currentConfig = computed(() => props.pptConfigs.find((item) => item.is_default) ?? props.pptConfigs[0] ?? null)
const modelCount = computed(() => props.preview ? 3 : activeModels.value.length)
const agentCount = computed(() => props.preview ? 5 : activeAgents.value.length)
const agentConfigCount = computed(() => props.preview ? 5 : activeAgentConfigs.value.length)
const dashboardHealth = computed(() => props.preview ? 'ok' : props.apiHealth)
const isReady = computed(() => dashboardHealth.value === 'ok' && modelCount.value > 0 && agentConfigCount.value > 0)

const providerRows = computed(() => {
  if (props.preview) return previewProviders
  return activeModels.value.slice(0, 3).map((model) => ({
    id: model.id,
    provider: model.provider,
    label: `${providerName(model.provider)} (${model.model_name})`,
  }))
})

const recentTasks = computed<DashboardTask[]>(() => {
  return props.preview ? previewTasks : []
})

const currentConfigName = computed(() => {
  if (props.preview) return 'PPTAgent · 默认工作流'
  if (!currentConfig.value) return '尚未配置工作流'
  return currentConfig.value.is_default ? `${currentConfig.value.name} · 默认工作流` : currentConfig.value.name
})

function providerName(provider: string) {
  const normalized = provider.toLowerCase()
  if (normalized.includes('openai')) return 'OpenAI'
  if (normalized.includes('deepseek')) return 'DeepSeek'
  if (normalized.includes('qwen') || normalized.includes('dashscope')) return 'Qwen'
  return provider
}

</script>

<template>
  <section class="dashboard-page">
    <div class="dashboard-primary">
      <header class="dashboard-welcome">
        <h2>早上好，Admin</h2>
        <p v-if="isReady">系统运行正常，所有组件就绪，您可以立即开始创建 PPT 任务。</p>
        <p v-else>完成模型与 Agent 配置后，即可开始创建 PPT 任务。</p>
      </header>

      <div class="dashboard-actions" aria-label="快捷操作">
        <button class="dashboard-action primary" type="button" @click="emit('navigate', 'ppt')">
          <Plus :size="24" />
          新建 PPT 任务
        </button>
        <button class="dashboard-action" type="button" @click="emit('navigate', 'models')">
          <Layers3 :size="22" />
          配置模型
        </button>
        <button class="dashboard-action" type="button" @click="emit('navigate', 'agents')">
          <Bot :size="22" />
          管理 Agent
        </button>
      </div>

      <section class="readiness-panel" aria-labelledby="readiness-title">
        <h3 id="readiness-title">准备状态</h3>
        <div class="readiness-flow">
          <div class="readiness-step">
            <span class="readiness-icon"><Layers3 :size="26" /></span>
            <div>
              <strong>Model <CircleCheck v-if="modelCount > 0" :size="16" /></strong>
              <span>{{ modelCount }}/{{ modelCount || 1 }} 已就绪</span>
            </div>
          </div>
          <ChevronRight class="readiness-chevron" :size="24" />
          <div class="readiness-step">
            <span class="readiness-icon"><Bot :size="26" /></span>
            <div>
              <strong>Agent <CircleCheck v-if="agentConfigCount > 0" :size="16" /></strong>
              <span>{{ agentConfigCount }}/{{ agentCount || agentConfigCount || 1 }} 已就绪</span>
            </div>
          </div>
          <ChevronRight class="readiness-chevron" :size="24" />
          <div class="readiness-step">
            <span class="readiness-icon"><FileText :size="26" /></span>
            <div>
              <strong>PPT <CircleCheck v-if="isReady" :size="16" /></strong>
              <span>{{ isReady ? '生成能力正常' : '等待配置' }}</span>
            </div>
          </div>
        </div>
        <div class="ready-strip" :class="{ 'is-pending': !isReady }">
          <CircleCheck v-if="isReady" :size="20" />
          <Clock3 v-else :size="20" />
          <span>{{ isReady ? '工作流已就绪，可随时创建 PPT 任务' : '工作流尚未完全就绪，请先完成必要配置' }}</span>
        </div>
      </section>

      <section class="recent-panel" aria-labelledby="recent-title">
        <div class="recent-head">
          <h3 id="recent-title">最近任务</h3>
        </div>

        <div v-if="recentTasks.length" class="recent-table" role="table" aria-label="最近 PPT 任务">
          <div class="recent-row recent-row-head" role="row">
            <span role="columnheader">任务名称</span>
            <span role="columnheader">状态</span>
            <span role="columnheader">更新时间</span>
            <span role="columnheader">操作</span>
          </div>
          <button
            v-for="task in recentTasks"
            :key="task.id"
            class="recent-row recent-task-row"
            type="button"
            role="row"
            @click="emit('navigate', 'ppt')"
          >
            <span class="task-name" role="cell">
              <span class="task-icon"><FileChartColumn :size="18" /></span>
              <strong>{{ task.title }}</strong>
            </span>
            <span class="task-status" :class="`tone-${task.tone}`" role="cell">
              <i aria-hidden="true" />
              {{ task.status }}
            </span>
            <time role="cell">{{ task.updatedAt }}</time>
            <span class="task-view" role="cell">
              查看
            </span>
          </button>
        </div>

        <div v-else class="recent-empty">
          <Presentation :size="24" />
          <div>
            <strong>还没有 PPT 任务</strong>
            <span>当前版本不保存任务历史，请前往 PPT 页面创建任务。</span>
          </div>
          <button type="button" @click="emit('navigate', 'ppt')">创建任务</button>
        </div>

        <button class="recent-more" type="button" @click="emit('navigate', 'logs')">
          查看全部运行记录
          <ArrowRight :size="17" />
        </button>
      </section>
    </div>

    <aside class="current-config-panel" aria-labelledby="current-config-title">
      <h3 id="current-config-title">当前配置</h3>

      <div class="config-section">
        <span class="config-label">工作流</span>
        <button class="workflow-config" type="button" @click="emit('navigate', 'agents')">
          <FileText :size="19" />
          <strong>{{ currentConfigName }}</strong>
          <ChevronRight :size="18" />
        </button>
        <span class="config-label description-label">说明</span>
        <p>{{ currentConfig?.description || (preview ? '通用演示文稿生成工作流' : '为 PPT 生成任务提供模型与节点配置。') }}</p>
      </div>

      <div class="config-section provider-section">
        <span class="config-label">模型绑定</span>
        <div v-if="providerRows.length" class="provider-list">
          <div v-for="model in providerRows" :key="model.id" class="provider-binding">
            <ProviderIcon class="dashboard-provider-icon" :provider="model.provider" :size="42" />
            <strong>{{ model.label }}</strong>
            <span><i aria-hidden="true" /> 已就绪</span>
          </div>
        </div>
        <div v-else class="provider-empty">
          <Database :size="20" />
          <span>尚未绑定模型</span>
        </div>
        <button class="manage-config" type="button" @click="emit('navigate', 'models')">
          管理模型配置
          <ArrowRight :size="16" />
        </button>
      </div>
    </aside>
  </section>
</template>
