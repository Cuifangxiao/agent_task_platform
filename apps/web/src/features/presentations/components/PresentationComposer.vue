<script setup lang="ts">
import { AlertCircle, Loader2, Paperclip, Square, X } from '@lucide/vue'

import type { AgentConfig } from '@/types'

defineProps<{
  canSubmit: boolean
  configId: string
  configs: AgentConfig[]
  error: string
  files: File[]
  mode: 'quick' | 'step_by_step'
  running: boolean
  text: string
  uploadState: string
}>()

const emit = defineEmits<{
  addFiles: [files: File[]]
  removeFile: [index: number]
  stop: []
  submit: []
  updateConfigId: [value: string]
  updateMode: [value: 'quick' | 'step_by_step']
  updateText: [value: string]
}>()

function onFilesSelected(event: Event) {
  const input = event.target as HTMLInputElement
  emit('addFiles', Array.from(input.files ?? []))
  input.value = ''
}
</script>

<template>
  <form class="composer-panel" @submit.prevent="$emit('submit')">
    <div class="composer-frame">
      <div class="composer-box">
        <textarea
          :value="text"
          rows="4"
          placeholder="例如：帮我做一份 AI Agent 平台 MVP 汇报，面向投资人，风格简洁专业，控制在 10 页左右。"
          @input="$emit('updateText', ($event.target as HTMLTextAreaElement).value)"
        />
        <div v-if="files.length" class="file-chip-list">
          <div v-for="(file, index) in files" :key="`${file.name}-${index}`" class="file-chip">
            <div class="file-chip-main">
              <Paperclip :size="14" />
              <span>{{ file.name }}</span>
            </div>
            <button class="chip-close" type="button" @click="$emit('removeFile', index)">
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
              type="file"
              multiple
              accept=".pdf,.txt,.md,.markdown,.csv,.json,.yaml,.yml,.docx,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              @change="onFilesSelected"
            />
          </label>
          <label class="config-picker">
            <span>使用配置</span>
            <select :value="configId" :disabled="running" @change="$emit('updateConfigId', ($event.target as HTMLSelectElement).value)">
              <option v-for="config in configs" :key="config.id" :value="String(config.id)">
                {{ config.name }}{{ config.is_default ? ' / 默认' : '' }}
              </option>
            </select>
          </label>
          <label class="config-picker">
            <span>生成模式</span>
            <select :value="mode" :disabled="running" @change="$emit('updateMode', ($event.target as HTMLSelectElement).value as 'quick' | 'step_by_step')">
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
          <button v-if="running" class="btn ghost" type="button" @click="$emit('stop')">
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
</template>
