<script setup lang="ts">
import { AlertCircle, Loader2, Paperclip, Send, X } from '@lucide/vue'

import type { AgentConfig } from '@/types'

defineProps<{
  canSubmit: boolean
  configId: string
  configs: AgentConfig[]
  error: string
  files: File[]
  submitting: boolean
  text: string
  uploadState: string
}>()

const emit = defineEmits<{
  addFiles: [files: File[]]
  removeFile: [index: number]
  submit: []
  updateConfigId: [value: string]
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
          :disabled="submitting"
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
            <button class="chip-close" type="button" :disabled="submitting" @click="$emit('removeFile', index)">
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
          <label class="btn ghost file-picker" :class="{ disabled: submitting }">
            <Paperclip :size="16" />
            添加资料
            <input
              type="file"
              multiple
              :disabled="submitting"
              accept=".pdf,.txt,.md,.markdown,.csv,.json,.yaml,.yml,.docx,.png,.jpg,.jpeg,.webp,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg,image/webp"
              @change="onFilesSelected"
            />
          </label>
          <label class="config-picker">
            <span>使用配置</span>
            <select
              :value="configId"
              :disabled="submitting"
              @change="$emit('updateConfigId', ($event.target as HTMLSelectElement).value)"
            >
              <option v-for="config in configs" :key="config.id" :value="String(config.id)">
                {{ config.name }}{{ config.is_default ? ' / 默认' : '' }}
              </option>
            </select>
          </label>
        </div>

        <div class="composer-footnote">
          <span class="chat-note">提交后将持续生成到本地 PPT 完成，无需中途确认。</span>
          <span v-if="uploadState" class="chat-note">{{ uploadState }}</span>
        </div>
        <div class="composer-toolbar-actions">
          <button class="btn primary send-btn" type="submit" :disabled="!canSubmit">
            <Loader2 v-if="submitting" class="spin" :size="18" />
            <Send v-else :size="17" />
            <span>{{ submitting ? '正在提交' : '创建任务' }}</span>
          </button>
        </div>
      </div>
    </div>
  </form>
</template>

<style scoped>
.file-picker.disabled {
  cursor: not-allowed;
  opacity: 0.56;
}
</style>
