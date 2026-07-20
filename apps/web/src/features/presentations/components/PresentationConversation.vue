<script setup lang="ts">
import {
  Activity,
  Bot,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  Expand,
  FileImage,
  LoaderCircle,
  UserRound,
  XCircle,
} from '@lucide/vue'
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import {
  projectPresentationConversation,
  type PresentationConversationMessage,
} from '../lib/conversationProjection'
import {
  compactTaskEvents,
  eventLabel,
  overallTaskProgress,
  progressLabel,
  resolveResourceUrl,
  stageLabel,
  taskStatusLabel,
} from '../lib/taskProjection'
import { formatDateTime } from '../lib/presentationUtils'
import type { PptAgentEvent, PptPagePreview, PptTask } from '../types'

const props = defineProps<{
  task: PptTask
  events: PptAgentEvent[]
  submittedDescription: string
  previews: PptPagePreview[]
  sources: Record<string, string>
}>()

const emit = defineEmits<{
  'open-preview': [preview: PptPagePreview]
}>()

const conversationBody = ref<HTMLElement | null>(null)
const shouldFollowLatest = ref(true)

const conversation = computed(() => projectPresentationConversation({
  ...props.task,
  description: props.submittedDescription.trim() || props.task.description,
}, props.events))

const overallProgress = computed(() => overallTaskProgress(props.task, props.events))
const compactedEvents = computed(() => compactTaskEvents(props.events))
const visibleTechnicalEvents = computed(() => compactedEvents.value.slice(-20).reverse())
const hiddenTechnicalEventCount = computed(() => Math.max(
  0,
  compactedEvents.value.length - visibleTechnicalEvents.value.length,
))
const collapsedTechnicalEventCount = computed(() => (
  props.events.length - compactedEvents.value.length
))

const previewMessageKey = computed(() => {
  const messages = conversation.value.messages
  return messages.find((message) => message.kind === 'page_progress')?.key
    ?? messages.find((message) => message.kind === 'completion')?.key
    ?? ''
})

const visiblePreviews = computed(() => props.previews.slice(-4))
const latestMessageSignature = computed(() => {
  const latest = conversation.value.messages.at(-1)
  return latest ? `${latest.key}:${latest.updatedSequence}:${props.previews.length}` : ''
})

onMounted(() => {
  void nextTick(scrollToLatest)
})

watch(latestMessageSignature, () => {
  if (shouldFollowLatest.value) void nextTick(scrollToLatest)
}, { flush: 'post' })

watch(() => props.task.task_id, () => {
  shouldFollowLatest.value = true
  void nextTick(scrollToLatest)
}, { flush: 'post' })

function scrollToLatest() {
  const element = conversationBody.value
  if (!element) return
  const latest = element.querySelector<HTMLElement>('.message-row:last-child')
  if (!latest) {
    element.scrollTop = element.scrollHeight
    return
  }
  const target = latest.getBoundingClientRect().top
    - element.getBoundingClientRect().top
    + element.scrollTop
    - 12
  element.scrollTop = Math.max(0, target)
}

function trackConversationScroll() {
  const element = conversationBody.value
  if (!element) return
  shouldFollowLatest.value = element.scrollHeight - element.scrollTop - element.clientHeight < 88
}

function isTerminalMessage(message: PresentationConversationMessage) {
  return message.kind === 'completion'
    || message.kind === 'error'
    || message.kind === 'cancelled'
}

function messageStatusLabel(message: PresentationConversationMessage) {
  if (message.kind === 'request') return '已发送'
  if (message.kind === 'completion') return '已完成'
  if (message.kind === 'error') return '失败'
  if (message.kind === 'cancelled') return '已取消'
  if (message.phaseStatus === 'done') return '已完成'
  return '进行中'
}

function previewTitle(preview: PptPagePreview, index: number) {
  return preview.page?.title
    || preview.resource.name
    || `第 ${preview.page?.page_index ?? index + 1} 页`
}

function previewSource(preview: PptPagePreview) {
  return props.sources[preview.resource.resource_id]
    || (preview.resource.url ? resolveResourceUrl(preview.resource.url) : '')
}

function eventTone(event: PptAgentEvent) {
  if (event.type === 'TASK_FAILED') return 'error'
  if (event.type === 'TASK_COMPLETED' || event.type === 'STEP_COMPLETED' || event.type === 'ARTIFACT_READY') {
    return 'done'
  }
  return 'neutral'
}
</script>

<template>
  <section class="presentation-conversation" aria-labelledby="presentation-conversation-title">
    <header class="conversation-head">
      <div class="conversation-heading">
        <span class="conversation-heading-icon" aria-hidden="true"><Bot :size="18" /></span>
        <div>
          <h3 id="presentation-conversation-title">PPTAgent</h3>
          <p>{{ task.status === 'running' ? '正在与你一起制作演示文稿' : '本次演示文稿会话' }}</p>
        </div>
      </div>
      <div class="conversation-state" :class="`is-${task.status}`">
        <LoaderCircle v-if="task.status === 'running'" class="spin" :size="14" aria-hidden="true" />
        <CheckCircle2 v-else-if="task.status === 'succeeded'" :size="14" aria-hidden="true" />
        <XCircle v-else :size="14" aria-hidden="true" />
        <span>{{ taskStatusLabel(task.status) }}</span>
        <strong v-if="task.status === 'running'">{{ overallProgress }}%</strong>
      </div>
    </header>

    <div ref="conversationBody" class="conversation-body" @scroll.passive="trackConversationScroll">
      <ol
        class="message-list"
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-label="PPT 会话消息"
      >
        <li
          v-for="message in conversation.messages"
          :key="message.key"
          class="message-row"
          :class="[
            `is-${message.role}`,
            `is-${message.kind}`,
            { 'is-wide': message.key === previewMessageKey && previews.length > 0 },
          ]"
        >
          <span class="message-avatar" aria-hidden="true">
            <UserRound v-if="message.role === 'user'" :size="17" />
            <Bot v-else :size="17" />
          </span>

          <article
            class="message-bubble"
            :aria-current="message.phaseStatus === 'active' ? 'step' : undefined"
          >
            <div class="message-meta">
              <strong>{{ message.role === 'user' ? '你' : 'PPTAgent' }}</strong>
              <time v-if="message.updatedAt || message.createdAt">
                {{ formatDateTime(message.updatedAt || message.createdAt) }}
              </time>
            </div>

            <div class="message-title-row">
              <span class="message-status-icon" :class="`is-${message.phaseStatus || 'done'}`" aria-hidden="true">
                <UserRound v-if="message.kind === 'request'" :size="15" />
                <LoaderCircle v-else-if="message.phaseStatus === 'active'" class="spin" :size="15" />
                <CircleAlert v-else-if="message.kind === 'error'" :size="15" />
                <XCircle v-else-if="message.kind === 'cancelled'" :size="15" />
                <CheckCircle2 v-else :size="15" />
              </span>
              <h4>{{ message.title }}</h4>
              <span class="message-state-label">{{ messageStatusLabel(message) }}</span>
            </div>

            <p class="message-copy">{{ message.content }}</p>

            <div
              v-if="message.progress && message.progress.total > 0"
              class="message-progress"
            >
              <div class="message-progress-copy">
                <span>页面进度</span>
                <strong>{{ message.progress.current }}/{{ message.progress.total }} 页</strong>
              </div>
              <div
                class="message-progress-track"
                role="progressbar"
                :aria-label="`${message.title} ${message.progress.percent ?? 0}%`"
                :aria-valuenow="message.progress.percent ?? 0"
                aria-valuemin="0"
                aria-valuemax="100"
              >
                <span :style="{ width: `${message.progress.percent ?? 0}%` }" />
              </div>
            </div>

            <div
              v-if="message.key === previewMessageKey && previews.length > 0"
              class="message-preview-block"
            >
              <div class="message-preview-head">
                <span><FileImage :size="15" /> 已生成页面</span>
                <strong>{{ previews.length }} 页</strong>
              </div>
              <div class="message-preview-grid">
                <button
                  v-for="(preview, index) in visiblePreviews"
                  :key="preview.resource.resource_id"
                  class="message-preview"
                  type="button"
                  :disabled="!previewSource(preview)"
                  :aria-label="`查看${previewTitle(preview, index)}`"
                  @click="emit('open-preview', preview)"
                >
                  <img
                    v-if="previewSource(preview)"
                    :src="previewSource(preview)"
                    :alt="previewTitle(preview, index)"
                  />
                  <span v-else class="message-preview-placeholder" aria-hidden="true">
                    <FileImage :size="22" />
                  </span>
                  <span class="message-preview-caption">
                    <span>{{ previewTitle(preview, index) }}</span>
                    <Expand :size="13" aria-hidden="true" />
                  </span>
                </button>
              </div>
              <p v-if="previews.length > visiblePreviews.length" class="message-preview-more">
                最近 4 页显示在会话中，其余 {{ previews.length - visiblePreviews.length }} 页可在页面预览区查看。
              </p>
            </div>

            <p v-if="isTerminalMessage(message)" class="terminal-note">
              {{ message.kind === 'completion' ? '可以下载本次结果，或在下方基于当前需求创建一个新版本。' : '你可以调整需求后创建一个新的 PPT 会话。' }}
            </p>
          </article>
        </li>
      </ol>

      <details class="technical-events">
        <summary>
          <span><Activity :size="16" aria-hidden="true" /> 技术日志</span>
          <span class="technical-event-count">
            {{ events.length }} 条事件
            <span v-if="collapsedTechnicalEventCount"> · 已合并 {{ collapsedTechnicalEventCount }} 条重复事件</span>
          </span>
          <ChevronDown class="technical-chevron" :size="16" aria-hidden="true" />
        </summary>

        <div v-if="events.length === 0" class="technical-empty">
          <Clock3 :size="17" aria-hidden="true" />
          <span>任务开始后，诊断信息会记录在这里。</span>
        </div>

        <div v-else class="technical-event-list">
          <p v-if="hiddenTechnicalEventCount" class="technical-limit-note">
            已隐藏较早的 {{ hiddenTechnicalEventCount }} 组日志，仅显示最近 20 组。
          </p>
          <article
            v-for="item in visibleTechnicalEvents"
            :key="item.event.event_id || item.event.sequence"
            class="technical-event"
            :class="`is-${eventTone(item.event)}`"
          >
            <CheckCircle2 v-if="eventTone(item.event) === 'done'" :size="14" aria-hidden="true" />
            <CircleAlert v-else-if="eventTone(item.event) === 'error'" :size="14" aria-hidden="true" />
            <span v-else class="technical-dot" aria-hidden="true" />
            <div>
              <div class="technical-event-title">
                <strong>{{ item.event.message || eventLabel(item.event.type) }}</strong>
                <span v-if="item.repeatCount > 1">重复 {{ item.repeatCount }} 次</span>
              </div>
              <p>
                <span>#{{ item.event.sequence }}</span>
                <span>{{ stageLabel(item.event.stage) }}</span>
                <span v-if="progressLabel(item.event)">{{ progressLabel(item.event) }}</span>
                <time v-if="item.event.created_at">{{ formatDateTime(item.event.created_at) }}</time>
              </p>
            </div>
          </article>
        </div>
      </details>
    </div>
  </section>
</template>

<style scoped>
.presentation-conversation {
  min-width: 0;
  min-height: 520px;
  height: 100%;
  overflow: hidden;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}

.conversation-head {
  min-width: 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  border-bottom: 1px solid var(--line);
  padding: 14px 18px;
}

.conversation-heading,
.conversation-state,
.message-meta,
.message-title-row,
.message-preview-head,
.message-preview-head > span,
.technical-events summary > span:first-child,
.technical-event-title,
.technical-event p {
  display: flex;
  align-items: center;
}

.conversation-heading {
  min-width: 0;
  gap: 10px;
}

.conversation-heading-icon,
.message-avatar {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 8px;
}

.conversation-heading-icon {
  width: 34px;
  height: 34px;
  background: var(--accent-soft);
  color: var(--accent-strong);
}

.conversation-heading h3 {
  color: var(--text);
  font-size: 15px;
}

.conversation-heading p {
  margin-top: 1px;
  color: var(--muted);
  font-size: 11px;
}

.conversation-state {
  flex: 0 0 auto;
  gap: 5px;
  border-radius: 999px;
  background: var(--panel-soft);
  color: var(--muted);
  padding: 5px 9px;
  font-size: 11px;
}

.conversation-state strong {
  color: inherit;
  font-variant-numeric: tabular-nums;
}

.conversation-state.is-running { background: var(--accent-soft); color: var(--accent-strong); }
.conversation-state.is-succeeded { background: var(--success-soft); color: var(--success); }
.conversation-state.is-failed { background: var(--danger-soft); color: var(--danger); }

.conversation-body {
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  display: grid;
  align-content: start;
  gap: 18px;
  background: var(--panel-soft);
  padding: 22px 18px 28px;
  scrollbar-gutter: stable;
}

.message-list {
  display: grid;
  gap: 20px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.message-row {
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 9px;
}

.message-row.is-user {
  flex-direction: row-reverse;
}

.message-avatar {
  width: 30px;
  height: 30px;
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--accent-strong);
}

.is-user .message-avatar {
  border-color: rgba(18, 124, 112, 0.2);
  background: var(--accent);
  color: #fff;
}

.message-bubble {
  width: min(76%, 680px);
  min-width: 0;
  display: grid;
  gap: 9px;
  border: 1px solid var(--line);
  border-radius: 4px 8px 8px;
  background: var(--panel);
  padding: 13px 14px;
  box-shadow: 0 5px 18px rgba(15, 35, 42, 0.035);
}

.is-user .message-bubble {
  border-color: rgba(18, 124, 112, 0.24);
  border-radius: 8px 4px 8px 8px;
  background: var(--accent-soft);
}

.is-wide .message-bubble {
  width: min(92%, 820px);
}

.is-error .message-bubble { border-color: rgba(180, 35, 24, 0.28); }
.is-cancelled .message-bubble { background: #fafafa; }

.message-meta {
  justify-content: space-between;
  gap: 10px;
  color: var(--muted-2);
  font-size: 10px;
}

.message-meta strong {
  color: var(--muted);
  font-size: 11px;
  font-weight: 700;
}

.message-title-row {
  min-width: 0;
  gap: 7px;
}

.message-title-row h4 {
  min-width: 0;
  margin: 0;
  color: var(--text);
  font-size: 13px;
  line-height: 1.35;
}

.message-status-icon {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  color: var(--success);
}

.message-status-icon.is-active { color: var(--accent); }
.is-error .message-status-icon { color: var(--danger); }
.is-cancelled .message-status-icon { color: var(--muted); }

.message-state-label {
  flex: 0 0 auto;
  margin-left: auto;
  color: var(--muted-2);
  font-size: 10px;
}

.message-copy {
  overflow-wrap: anywhere;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
}

.is-user .message-copy {
  color: var(--text);
}

.message-progress {
  display: grid;
  gap: 6px;
  border-top: 1px solid var(--line);
  padding-top: 9px;
}

.message-progress-copy {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--muted);
  font-size: 10px;
}

.message-progress-copy strong {
  color: var(--accent-strong);
  font-variant-numeric: tabular-nums;
}

.message-progress-track {
  height: 5px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(100, 116, 139, 0.13);
}

.message-progress-track > span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transition: width 180ms ease;
}

.message-preview-block {
  min-width: 0;
  display: grid;
  gap: 9px;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}

.message-preview-head {
  justify-content: space-between;
  gap: 10px;
  color: var(--muted);
  font-size: 11px;
}

.message-preview-head > span { gap: 6px; }
.message-preview-head strong { color: var(--text); font-size: 11px; }

.message-preview-grid {
  min-width: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.message-preview {
  min-width: 0;
  overflow: hidden;
  display: grid;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--panel);
  padding: 0;
  text-align: left;
}

.message-preview:hover {
  border-color: var(--accent);
}

.message-preview:focus-visible {
  outline: 3px solid rgba(18, 124, 112, 0.16);
  outline-offset: 1px;
}

.message-preview img,
.message-preview-placeholder {
  width: 100%;
  aspect-ratio: 16 / 9;
}

.message-preview img {
  display: block;
  object-fit: contain;
  background: #fff;
}

.message-preview-placeholder {
  display: grid;
  place-items: center;
  background: #f2f5f6;
  color: var(--muted-2);
}

.message-preview-caption {
  min-width: 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  padding: 6px 8px;
  font-size: 10px;
}

.message-preview-caption > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.message-preview-more,
.terminal-note {
  color: var(--muted-2);
  font-size: 10px;
}

.terminal-note {
  border-top: 1px solid var(--line);
  padding-top: 9px;
}

.technical-events {
  width: min(76%, 680px);
  margin-left: 39px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.74);
}

.technical-events summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 8px;
  align-items: center;
  cursor: pointer;
  list-style: none;
  color: var(--text);
  padding: 10px 12px;
  font-size: 11px;
  font-weight: 700;
}

.technical-events summary::-webkit-details-marker { display: none; }
.technical-events summary > span:first-child { gap: 6px; }
.technical-events[open] .technical-chevron { transform: rotate(180deg); }
.technical-chevron { color: var(--muted); transition: transform 160ms ease; }
.technical-event-count { color: var(--muted-2); font-size: 9px; font-weight: 500; }

.technical-event-list {
  max-height: 320px;
  overflow-y: auto;
  display: grid;
  gap: 9px;
  border-top: 1px solid var(--line);
  padding: 11px 12px;
}

.technical-event {
  min-width: 0;
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr);
  gap: 7px;
  align-items: start;
  color: var(--muted-2);
}

.technical-event.is-done > svg { color: var(--success); }
.technical-event.is-error > svg { color: var(--danger); }

.technical-dot {
  width: 6px;
  height: 6px;
  margin: 4px;
  border-radius: 999px;
  background: var(--muted-2);
}

.technical-event-title {
  justify-content: space-between;
  gap: 8px;
}

.technical-event-title strong {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--text);
  font-size: 10px;
  font-weight: 650;
}

.technical-event-title > span {
  flex: 0 0 auto;
  border-radius: 999px;
  background: var(--panel-soft);
  color: var(--muted);
  padding: 2px 5px;
  font-size: 8px;
}

.technical-event p {
  flex-wrap: wrap;
  gap: 3px 8px;
  margin-top: 3px;
  color: var(--muted-2);
  font-size: 8px;
}

.technical-event p time { margin-left: auto; }

.technical-empty,
.technical-limit-note {
  color: var(--muted);
  font-size: 10px;
}

.technical-empty {
  display: flex;
  align-items: center;
  gap: 7px;
  border-top: 1px solid var(--line);
  padding: 12px;
}

.technical-limit-note {
  border-radius: 6px;
  background: var(--panel-soft);
  padding: 7px 8px;
}

@media (max-width: 760px) {
  .conversation-head { align-items: flex-start; }
  .conversation-heading p { display: none; }
  .conversation-body { padding: 18px 12px 24px; }
  .message-bubble,
  .is-wide .message-bubble,
  .technical-events { width: calc(100% - 39px); }
  .message-preview-grid { grid-template-columns: 1fr; }
  .technical-event-count { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .message-progress-track > span,
  .technical-chevron,
  .spin {
    animation: none !important;
    transition: none;
  }
}
</style>
