<script setup lang="ts">
import { nextTick, ref } from 'vue'
import {
  AlertCircle,
  CheckCircle2,
  Download,
  Loader2,
  Maximize2,
  Paperclip,
} from '@lucide/vue'

import {
  currentProgressLabel,
  currentProgressStageLabel,
  progressChecklist,
  slideStatusLabel,
  slideStatusPillClass,
  statusStateLabel,
} from '../lib/progressView'
import type { ChatMessage, PresentationMessageAction } from '../types'

const props = defineProps<{
  conversationTitle: string | null
  messages: ChatMessage[]
  previewSrc: (requestId: string | undefined, thumbB64: string, pngPath: string) => string
  running: boolean
  slideFeedback: Record<number, string>
  styleFeedback: Record<number, string>
}>()

const emit = defineEmits<{ action: [action: PresentationMessageAction] }>()
const threadRef = ref<HTMLElement | null>(null)

async function scrollToBottom(smooth = true) {
  await nextTick()
  threadRef.value?.scrollTo({
    top: threadRef.value.scrollHeight,
    behavior: smooth ? 'smooth' : 'auto',
  })
}

function messageRequestId(message: ChatMessage) {
  return 'requestId' in message ? message.requestId : undefined
}

function preview(requestId: string | undefined, thumb: string, path: string, title: string, subtitle: string) {
  emit('action', { type: 'preview', src: props.previewSrc(requestId, thumb, path), title, subtitle })
}

defineExpose({ scrollToBottom })
</script>

<template>
  <section ref="threadRef" class="chat-thread">
    <article class="chat-message assistant">
      <div class="chat-avatar">AI</div>
      <div class="chat-bubble is-welcome">
        <div class="chat-label">PPT 助手</div>
        <strong>直接描述你想做的内容就行</strong>
        <p>你可以把页数、对象、风格、语气等要求自然写进一句话里，我会先整理大纲，再请你确认。</p>
        <span class="chat-note">
          {{ conversationTitle ? `当前查看：${conversationTitle}` : '当前是新的对话草稿。' }}
        </span>
      </div>
    </article>

    <article v-for="message in messages" :key="message.id" :class="['chat-message', message.role]">
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
            <li v-for="item in progressChecklist(message)" :key="item.key" :class="`is-${item.status}`">
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
                @click="preview(messageRequestId(message), slide.previewThumb, slide.previewPng, `Slide ${slide.index}`, slide.title)"
              >
                <img
                  v-if="previewSrc(messageRequestId(message), slide.previewThumb, slide.previewPng)"
                  class="slide-preview-thumb"
                  :src="previewSrc(messageRequestId(message), slide.previewThumb, slide.previewPng)"
                  :alt="`Slide ${slide.index} preview`"
                  loading="lazy"
                />
                <span v-else class="slide-preview-placeholder">Loading preview</span>
                <span class="slide-preview-action"><Maximize2 :size="14" /> Full preview</span>
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
                  :value="slideFeedback[slide.index] ?? ''"
                  class="outline-editor"
                  type="text"
                  :placeholder="`第 ${slide.index} 页修改意见，例如：减少文字，改成左右对比`"
                  :disabled="running"
                  @input="$emit('action', { type: 'update-slide-feedback', slideIndex: slide.index, value: ($event.target as HTMLInputElement).value })"
                />
                <button
                  class="btn ghost"
                  type="button"
                  :disabled="running"
                  @click="$emit('action', { type: 'regenerate-slide', slide })"
                >
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
                <div><strong>{{ step.label }}</strong><p>{{ step.message }}</p></div>
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
            :value="message.outlineMarkdown"
            class="outline-editor"
            rows="12"
            spellcheck="false"
            @input="$emit('action', { type: 'update-outline', messageId: message.id, value: ($event.target as HTMLTextAreaElement).value })"
          />
          <div v-if="message.awaitingConfirm" class="outline-actions">
            <button class="btn primary" type="button" :disabled="running" @click="$emit('action', { type: 'approve-outline', message })">
              <CheckCircle2 :size="18" />
              确认继续生成
            </button>
            <button class="btn ghost" type="button" :disabled="running" @click="$emit('action', { type: 'toggle-outline-editor', messageId: message.id })">
              {{ message.showEditor ? '收起大纲' : '编辑大纲' }}
            </button>
            <button class="btn ghost" type="button" :disabled="running" @click="$emit('action', { type: 'regenerate-outline', message })">
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
            :value="styleFeedback[message.id] ?? ''"
            class="outline-editor"
            rows="2"
            placeholder="可选：补充风格调整意见，例如更克制、减少装饰、提高数据对比度"
            @input="$emit('action', { type: 'update-style-feedback', messageId: message.id, value: ($event.target as HTMLTextAreaElement).value })"
          />
          <div class="style-options">
            <article v-for="option in message.options" :key="option.styleId" class="style-option-card">
              <button
                v-if="option.thumbB64 || option.coverPng"
                class="style-preview-frame"
                type="button"
                @click="preview(messageRequestId(message), option.thumbB64, option.coverPng, option.name, 'Style preview')"
              >
                <img
                  v-if="previewSrc(messageRequestId(message), option.thumbB64, option.coverPng)"
                  :src="previewSrc(messageRequestId(message), option.thumbB64, option.coverPng)"
                  :alt="option.name"
                  loading="lazy"
                />
                <span v-else class="slide-preview-placeholder">Loading preview</span>
                <span class="slide-preview-action"><Maximize2 :size="14" /> Full preview</span>
              </button>
              <div><strong>{{ option.name }}</strong><p>{{ option.tokensSummary }}</p></div>
              <button
                v-if="message.awaitingConfirm"
                class="btn primary"
                type="button"
                :disabled="running"
                @click="$emit('action', { type: 'choose-style', message, option })"
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
            <button class="btn success" type="button" @click="$emit('action', { type: 'download', requestId: message.requestId, fileName: message.fileName })">
              <Download :size="18" />
              下载 PPT
            </button>
            <span class="chat-note">{{ message.fileName }}</span>
          </div>
        </template>
      </div>
    </article>
  </section>
</template>
