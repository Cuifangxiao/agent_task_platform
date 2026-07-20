<script setup lang="ts">
import { AlertCircle, FileText, Loader2, Paperclip, Send, X } from '@lucide/vue'
import { computed } from 'vue'

import type { AgentConfig } from '@/types'

const props = defineProps<{
  canSubmit: boolean
  configId: string
  configs: AgentConfig[]
  error: string
  files: File[]
  submitting: boolean
  text: string
  uploadState: string
  mode?: 'brief' | 'conversation'
  title?: string
  helper?: string
  placeholder?: string
  submitLabel?: string
}>()

const emit = defineEmits<{
  addFiles: [files: File[]]
  removeFile: [index: number]
  submit: []
  updateConfigId: [value: string]
  updateText: [value: string]
}>()

const characterCount = computed(() => Array.from(props.text).length)
const isConversationMode = computed(() => props.mode === 'conversation')
const heading = computed(() => props.title || (isConversationMode.value ? '继续完善这份 PPT' : '描述你的演示需求'))
const supportingText = computed(() => props.helper || (
  isConversationMode.value
    ? '可以调整内容、页数或风格；发送后会生成一个新版本。'
    : '写清受众、目标、页数和视觉偏好，生成结果会更稳定。'
))
const textareaPlaceholder = computed(() => props.placeholder || (
  isConversationMode.value
    ? '例如：把第 3 页改成架构图，整体更适合投资人阅读。'
    : '例如：制作一份 AI Agent 平台 MVP 汇报，面向投资人，风格简洁专业，控制在 10 页左右。'
))
const actionLabel = computed(() => props.submitLabel || (isConversationMode.value ? '发送并生成' : '创建任务'))

function onFilesSelected(event: Event) {
  const input = event.target as HTMLInputElement
  emit('addFiles', Array.from(input.files ?? []))
  input.value = ''
}

function onTextareaKeydown(event: KeyboardEvent) {
  if (!isConversationMode.value || event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  if (props.canSubmit) emit('submit')
}
</script>

<template>
  <form
    class="composer-panel"
    :class="{ 'is-conversation': isConversationMode }"
    aria-labelledby="presentation-brief-title"
    @submit.prevent="$emit('submit')"
  >
    <div class="composer-heading">
      <div class="composer-heading-icon"><FileText :size="20" /></div>
      <div>
        <h3 id="presentation-brief-title">{{ heading }}</h3>
        <p>{{ supportingText }}</p>
      </div>
      <span class="character-count">{{ characterCount }}/5000</span>
    </div>

    <textarea
      id="presentation-brief"
      :value="text"
      :disabled="submitting"
      :rows="isConversationMode ? 3 : 5"
      maxlength="5000"
      aria-label="PPT 生成需求"
      :placeholder="textareaPlaceholder"
      @input="$emit('updateText', ($event.target as HTMLTextAreaElement).value)"
      @keydown="onTextareaKeydown"
    />

    <div v-if="files.length" class="file-chip-list" aria-label="已添加的参考资料">
      <div v-for="(file, index) in files" :key="`${file.name}-${index}`" class="file-chip">
        <div class="file-chip-main">
          <Paperclip :size="14" />
          <span>{{ file.name }}</span>
        </div>
        <button
          class="chip-close"
          type="button"
          :disabled="submitting"
          :aria-label="`移除 ${file.name}`"
          @click="$emit('removeFile', index)"
        >
          <X :size="14" />
        </button>
      </div>
    </div>

    <p v-if="error" class="form-error composer-error" role="alert">
      <AlertCircle :size="16" />
      {{ error }}
    </p>

    <div class="composer-footer">
      <div class="composer-controls">
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

        <label class="config-picker" for="presentation-config">
          <span>生成配置</span>
          <select
            id="presentation-config"
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

      <div class="composer-submit">
        <span v-if="uploadState" class="upload-state" role="status">{{ uploadState }}</span>
        <button class="btn primary send-btn" type="submit" :disabled="!canSubmit">
          <Loader2 v-if="submitting" class="spin" :size="18" />
          <Send v-else :size="17" />
          <span>{{ submitting ? '正在创建' : actionLabel }}</span>
        </button>
      </div>
    </div>
  </form>
</template>

<style scoped>
.composer-panel {
  min-width: 0;
  display: grid;
  gap: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 18px;
}

.composer-heading {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 11px;
  align-items: center;
}

.composer-heading-icon {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 7px;
  background: var(--accent-soft);
  color: var(--accent-strong);
}

.composer-heading h3 {
  color: var(--text);
  font-size: 15px;
}

.composer-heading p {
  margin-top: 2px;
  color: var(--muted);
  font-size: 12px;
}

.character-count {
  color: var(--muted-2);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.composer-panel textarea {
  min-height: 118px;
  resize: vertical;
  border-color: var(--line);
  background: #fbfcfc;
  padding: 14px;
  line-height: 1.65;
}

.composer-panel.is-conversation {
  gap: 10px;
  border-radius: 10px;
  padding: 13px;
  box-shadow: 0 12px 34px rgba(15, 35, 42, 0.07);
}

.composer-panel.is-conversation .composer-heading-icon {
  width: 34px;
  height: 34px;
}

.composer-panel.is-conversation .composer-heading p {
  font-size: 11px;
}

.composer-panel.is-conversation textarea {
  min-height: 76px;
  max-height: 180px;
  padding: 12px 13px;
}

.composer-panel.is-conversation .composer-footer {
  flex-wrap: wrap;
  align-items: end;
  padding-top: 10px;
}

.composer-panel.is-conversation .composer-controls {
  flex-wrap: wrap;
}

.composer-panel.is-conversation .config-picker {
  min-width: 0;
  flex: 1 1 210px;
  grid-template-columns: auto minmax(120px, 1fr);
}

.composer-panel.is-conversation .composer-submit {
  margin-left: auto;
}

.composer-footer,
.composer-controls,
.composer-submit {
  display: flex;
  gap: 10px;
  align-items: end;
}

.composer-footer {
  justify-content: space-between;
  border-top: 1px solid var(--line);
  padding-top: 14px;
}

.composer-controls {
  min-width: 0;
  flex: 1 1 auto;
}

.file-picker {
  position: relative;
  overflow: hidden;
  display: inline-flex;
  min-width: max-content;
  color: var(--text);
  font-size: 13px;
}

.file-picker input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

.file-picker.disabled {
  cursor: not-allowed;
  opacity: 0.56;
}

.file-picker:focus-within {
  outline: 3px solid rgba(18, 124, 112, 0.18);
  outline-offset: 2px;
}

.config-picker {
  min-width: min(360px, 48vw);
  display: grid;
  grid-template-columns: auto minmax(180px, 1fr);
  gap: 8px;
  align-items: center;
}

.config-picker span {
  color: var(--muted);
  font-size: 12px;
}

.config-picker select {
  min-height: 40px;
  padding-block: 7px;
}

.composer-submit {
  flex: 0 0 auto;
}

.upload-state {
  max-width: 260px;
  color: var(--muted);
  font-size: 11px;
  text-align: right;
}

.send-btn { min-width: 132px; }
.composer-error { margin: 0; }

.file-chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.file-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--panel-soft);
  padding: 6px 8px 6px 10px;
  color: var(--text);
  font-size: 12px;
}

.file-chip-main {
  min-width: 0;
  display: inline-flex;
  gap: 6px;
  align-items: center;
}

.file-chip-main span {
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chip-close {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  padding: 0;
}

.chip-close:hover { background: rgba(15, 23, 42, 0.06); }

@media (max-width: 720px) {
  .composer-footer,
  .composer-controls {
    align-items: stretch;
    flex-direction: column;
  }

  .config-picker {
    width: 100%;
    min-width: 0;
    grid-template-columns: 1fr;
  }

  .composer-submit {
    width: 100%;
    justify-content: space-between;
  }
}

@media (max-width: 480px) {
  .composer-heading { grid-template-columns: auto minmax(0, 1fr); }
  .character-count { grid-column: 2; }
  .composer-submit { align-items: stretch; flex-direction: column; }
  .send-btn { width: 100%; }
  .upload-state { max-width: none; text-align: left; }
}
</style>
