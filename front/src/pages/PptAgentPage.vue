<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { AlertCircle, Download, Play, Presentation, Square } from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
import NodeBindingList from '@/components/NodeBindingList.vue'
import SseProgressTimeline, { type TimelineEvent } from '@/components/SseProgressTimeline.vue'
import type { AgentConfig, AgentDefinition, ModelConfig, ParsedSseEvent, PptGeneratePayload, PptStreamChunk } from '@/types'

const props = defineProps<{
  configs: AgentConfig[]
  agents: AgentDefinition[]
  models: ModelConfig[]
  streamPpt: (payload: PptGeneratePayload, onEvent: (event: ParsedSseEvent) => void, signal: AbortSignal) => Promise<void>
}>()

const form = reactive({
  agent_config_id: '',
  topic: '',
  audience: '',
  goal: '',
  page_count: 8,
  language: 'zh-CN',
  style: 'concise business deck',
  source_text: '',
})

const running = ref(false)
const error = ref('')
const artifactUrl = ref('')
const requestId = ref('')
const events = ref<TimelineEvent[]>([])
const abortController = ref<AbortController | null>(null)

const selectedConfig = computed(() => props.configs.find((item) => item.id === Number(form.agent_config_id)) ?? null)

function useDefaultConfig() {
  const config = props.configs.find((item) => item.is_default) ?? props.configs[0]
  if (config) {
    form.agent_config_id = String(config.id)
  }
}

async function generate() {
  error.value = ''
  artifactUrl.value = ''
  events.value = []

  if (!form.agent_config_id) {
    error.value = '请先选择一个 active PPTAgent Config。'
    return
  }
  if (!form.topic.trim()) {
    error.value = '请输入 PPT 主题。'
    return
  }

  const controller = new AbortController()
  abortController.value = controller
  running.value = true

  try {
    await props.streamPpt(
      {
        agent_config_id: Number(form.agent_config_id),
        topic: form.topic.trim(),
        audience: form.audience.trim(),
        goal: form.goal.trim(),
        page_count: Number(form.page_count),
        language: form.language.trim(),
        style: form.style.trim(),
        source_text: form.source_text.trim(),
      },
      handleEvent,
      controller.signal,
    )
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      events.value.unshift({
        id: Date.now(),
        event: 'abort',
        stage: '已停止',
        message: '生成请求已停止。',
      })
    } else {
      error.value = err instanceof Error ? err.message : String(err)
    }
  } finally {
    running.value = false
    abortController.value = null
  }
}

function stop() {
  abortController.value?.abort()
}

function handleEvent(event: ParsedSseEvent) {
  const data = normalizeChunk(event.data)
  if (data.request_id) {
    requestId.value = data.request_id
  }
  if (data.artifact_url) {
    artifactUrl.value = data.artifact_url
  }
  if (data.error) {
    error.value = data.error
  }

  const tone: TimelineEvent['tone'] = data.error ? 'error' : data.done ? 'done' : 'normal'
  events.value = [
    {
      id: Date.now() + Math.floor(Math.random() * 1000),
      event: data.event_type || event.event,
      stage: data.stage || data.event_type || event.event,
      message: data.message || data.error || (data.done ? '生成完成。' : '已收到事件。'),
      sequence: data.sequence,
      tone,
    },
    ...events.value,
  ].slice(0, 80)
}

function normalizeChunk(value: unknown): PptStreamChunk {
  if (value && typeof value === 'object') {
    return value as PptStreamChunk
  }
  return { message: String(value) }
}

useDefaultConfig()
</script>

<template>
  <section class="page two-column">
    <div>
      <div class="page-head">
        <div>
          <h1>PPTAgent</h1>
          <p>选择一套 PPTAgent Config 后生成 PPT，不支持绕过节点绑定直接选择单模型。</p>
        </div>
      </div>

      <EmptyState
        v-if="configs.length === 0"
        :icon="Presentation"
        title="暂无可用 PPTAgent Config"
        text="请先在 Agent 管理里为 PPTAgent 的每个节点绑定 active Model Config。"
      />

      <form v-else class="editor-panel" @submit.prevent="generate">
        <label>
          PPTAgent Config
          <select v-model="form.agent_config_id" required>
            <option value="">请选择配置</option>
            <option v-for="config in configs" :key="config.id" :value="String(config.id)">
              {{ config.name }}{{ config.is_default ? ' / 默认' : '' }}
            </option>
          </select>
        </label>

        <div v-if="selectedConfig" class="selected-config">
          <strong>{{ selectedConfig.name }}</strong>
          <p>{{ selectedConfig.description || '无描述' }}</p>
          <NodeBindingList :config="selectedConfig" :agents="agents" :models="models" />
        </div>

        <label>
          主题
          <input v-model="form.topic" required maxlength="500" placeholder="例如：面向个人开发者的 Agent 平台 MVP 规划" />
        </label>

        <div class="form-grid">
          <label>
            受众
            <input v-model="form.audience" maxlength="500" placeholder="例如：solo developer" />
          </label>
          <label>
            页数
            <input v-model.number="form.page_count" type="number" min="1" max="30" />
          </label>
        </div>

        <label>
          目标
          <input v-model="form.goal" maxlength="500" placeholder="这份 PPT 需要说服或解释什么" />
        </label>

        <div class="form-grid">
          <label>
            语言
            <input v-model="form.language" maxlength="64" />
          </label>
          <label>
            风格
            <input v-model="form.style" maxlength="200" />
          </label>
        </div>

        <label>
          参考材料
          <textarea v-model="form.source_text" maxlength="50000" placeholder="可选：粘贴产品背景、文章、会议纪要或资料摘要" />
        </label>

        <p v-if="error" class="form-error">
          <AlertCircle :size="16" />
          {{ error }}
        </p>

        <div class="button-row">
          <button class="btn primary" type="submit" :disabled="running">
            <Play :size="18" />
            开始生成 PPT
          </button>
          <button class="btn ghost" type="button" :disabled="!running" @click="stop">
            <Square :size="18" />
            停止
          </button>
          <a v-if="artifactUrl" class="btn success" :href="artifactUrl" target="_blank" rel="noreferrer">
            <Download :size="18" />
            下载 PPT
          </a>
        </div>

        <p v-if="requestId" class="muted mono">request_id: {{ requestId }}</p>
      </form>
    </div>

    <SseProgressTimeline :events="events" :running="running" />
  </section>
</template>
