<script setup lang="ts">
import {
  Activity,
  ChevronDown,
  Circle,
  CircleCheck,
  CircleX,
  Clock3,
  FileInput,
  LayoutTemplate,
  ListChecks,
  Loader2,
  Package,
  Palette,
} from '@lucide/vue'
import { computed } from 'vue'

import {
  compactTaskEvents,
  eventLabel,
  isCancelledTaskEvent,
  progressLabel,
  projectTaskPhases,
  stageLabel,
  type PresentationPhaseId,
} from '../lib/taskProjection'
import { formatDateTime } from '../lib/presentationUtils'
import type { PptAgentEvent, PptTask } from '../types'

const props = defineProps<{
  events: PptAgentEvent[]
  task: PptTask
}>()

const phases = computed(() => projectTaskPhases(props.events, props.task))
const compactedEvents = computed(() => compactTaskEvents(props.events))
const visibleEvents = computed(() => compactedEvents.value.slice(-80).reverse())
const hiddenEventCount = computed(() => Math.max(0, compactedEvents.value.length - visibleEvents.value.length))
const collapsedEventCount = computed(() => props.events.length - compactedEvents.value.length)
const latestSequence = computed(() => props.events[props.events.length - 1]?.sequence ?? 0)

function phaseIcon(id: PresentationPhaseId) {
  switch (id) {
    case 'prepare': return FileInput
    case 'plan': return ListChecks
    case 'design': return Palette
    case 'generate': return LayoutTemplate
    case 'export': return Package
    case 'done': return CircleCheck
  }
}

function phaseStatusLabel(status: string) {
  switch (status) {
    case 'active': return '进行中'
    case 'done': return '已完成'
    case 'error': return '失败'
    case 'cancelled': return '已取消'
    default: return '等待中'
  }
}

function isLatestRunningEvent(event: PptAgentEvent) {
  return props.task.status === 'running' && event.sequence === latestSequence.value
}

function eventTone(event: PptAgentEvent) {
  if (isCancelledTaskEvent(event)) return 'cancelled'
  if (event.type === 'TASK_FAILED') return 'error'
  if (event.type === 'TASK_COMPLETED' || event.type === 'STEP_COMPLETED' || event.type === 'ARTIFACT_READY') return 'done'
  return 'neutral'
}
</script>

<template>
  <section class="progress-panel" aria-labelledby="task-progress-title">
    <div class="panel-head progress-panel-head">
      <div>
        <span class="section-kicker">EXECUTION</span>
        <h3 id="task-progress-title">生成进度</h3>
      </div>
      <span class="phase-count">{{ phases.filter((phase) => phase.status === 'done').length }}/{{ phases.length }} 阶段</span>
    </div>

    <ol class="phase-list" aria-label="PPT 生成阶段">
      <li v-for="phase in phases" :key="phase.id" class="phase-item" :class="`is-${phase.status}`">
        <div class="phase-marker" aria-hidden="true">
          <CircleCheck v-if="phase.status === 'done'" :size="18" />
          <CircleX v-else-if="phase.status === 'error' || phase.status === 'cancelled'" :size="18" />
          <Loader2 v-else-if="phase.status === 'active'" class="spin" :size="18" />
          <Circle v-else :size="16" />
        </div>
        <div class="phase-copy">
          <div class="phase-title-row">
            <span class="phase-name">
              <component :is="phaseIcon(phase.id)" :size="15" />
              {{ phase.label }}
            </span>
            <span class="phase-status">{{ phaseStatusLabel(phase.status) }}</span>
          </div>
          <p>{{ phase.message }}</p>
          <div v-if="phase.pageTotal || phase.eventCount" class="phase-meta">
            <span v-if="phase.pageTotal">{{ phase.pageCurrent }}/{{ phase.pageTotal }} 页</span>
            <span v-if="phase.eventCount">{{ phase.eventCount }} 条事件</span>
            <time v-if="phase.updatedAt">{{ formatDateTime(phase.updatedAt) }}</time>
          </div>
          <div
            v-if="phase.status === 'active' && phase.progressPercent > 0"
            class="phase-progress"
            role="progressbar"
            :aria-label="`${phase.label} ${phase.progressPercent}%`"
            :aria-valuenow="phase.progressPercent"
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <span :style="{ width: `${phase.progressPercent}%` }" />
          </div>
        </div>
      </li>
    </ol>

    <details class="event-details">
      <summary>
        <span><Activity :size="16" /> 技术日志</span>
        <span class="event-summary-count">
          {{ events.length }} 条
          <span v-if="collapsedEventCount">· 已归并 {{ collapsedEventCount }} 条重复</span>
        </span>
        <ChevronDown class="details-chevron" :size="16" />
      </summary>

      <div v-if="events.length === 0" class="event-empty">
        <Clock3 :size="18" />
        <p>任务启动后，详细事件会显示在这里。</p>
      </div>

      <div v-else class="event-log">
        <p v-if="hiddenEventCount" class="event-limit-note">较早的 {{ hiddenEventCount }} 组事件已隐藏，保留最近 80 组。</p>
        <article
          v-for="item in visibleEvents"
          :key="item.event.event_id || item.event.sequence"
          class="event-row"
          :class="`is-${eventTone(item.event)}`"
        >
          <Loader2 v-if="isLatestRunningEvent(item.event)" class="spin" :size="15" />
          <CircleCheck v-else-if="eventTone(item.event) === 'done'" :size="15" />
          <CircleX v-else-if="eventTone(item.event) === 'error' || eventTone(item.event) === 'cancelled'" :size="15" />
          <Circle v-else :size="13" />
          <div>
            <div class="event-row-title">
              <strong>{{ item.event.message || eventLabel(item.event.type) }}</strong>
              <span v-if="item.repeatCount > 1" class="repeat-badge">重复 {{ item.repeatCount }} 次</span>
            </div>
            <div class="event-row-meta">
              <span>#{{ item.event.sequence }}</span>
              <span>{{ stageLabel(item.event.stage) }}</span>
              <span>{{ eventLabel(item.event.type) }}</span>
              <span v-if="progressLabel(item.event)">{{ progressLabel(item.event) }}</span>
              <time v-if="item.event.created_at">{{ formatDateTime(item.event.created_at) }}</time>
            </div>
          </div>
        </article>
      </div>
    </details>
  </section>
</template>

<style scoped>
.progress-panel {
  min-width: 0;
  display: grid;
  gap: 16px;
  align-content: start;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 18px;
}

.progress-panel-head h3 {
  margin-top: 4px;
  color: var(--text);
  font-size: 17px;
}

.section-kicker {
  color: var(--accent-strong);
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.12em;
}

.phase-count,
.event-summary-count {
  color: var(--muted);
  font-size: 12px;
}

.phase-list {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.phase-item {
  position: relative;
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: 10px;
  min-width: 0;
  padding: 0 0 18px;
}

.phase-item:last-child {
  padding-bottom: 0;
}

.phase-item:not(:last-child)::before {
  content: '';
  position: absolute;
  top: 20px;
  bottom: 2px;
  left: 9px;
  width: 1px;
  background: var(--line);
}

.phase-item.is-done:not(:last-child)::before {
  background: rgba(22, 101, 52, 0.28);
}

.phase-marker {
  position: relative;
  z-index: 1;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: 999px;
  background: var(--panel);
  color: var(--muted-2);
}

.is-done .phase-marker { color: var(--success); }
.is-active .phase-marker { color: var(--accent); }
.is-error .phase-marker { color: var(--danger); }
.is-cancelled .phase-marker { color: var(--muted); }

.phase-copy {
  min-width: 0;
  display: grid;
  gap: 5px;
}

.phase-title-row,
.phase-name,
.phase-meta,
.event-row-title,
.event-row-meta,
.event-details summary > span:first-child {
  display: flex;
  align-items: center;
}

.phase-title-row {
  justify-content: space-between;
  gap: 10px;
}

.phase-name {
  gap: 7px;
  color: var(--text);
  font-size: 13px;
  font-weight: 720;
}

.phase-status {
  color: var(--muted-2);
  font-size: 11px;
}

.is-active .phase-status { color: var(--accent-strong); font-weight: 700; }
.is-error .phase-status { color: var(--danger); }
.is-done .phase-status { color: var(--success); }

.phase-copy p {
  overflow-wrap: anywhere;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}

.phase-meta {
  flex-wrap: wrap;
  gap: 5px 10px;
  color: var(--muted-2);
  font-size: 10px;
}

.phase-meta time {
  margin-left: auto;
}

.phase-progress {
  height: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--panel-soft);
}

.phase-progress span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
}

.event-details {
  border-top: 1px solid var(--line);
  padding-top: 14px;
}

.event-details summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 8px;
  align-items: center;
  color: var(--text);
  cursor: pointer;
  list-style: none;
  font-size: 13px;
  font-weight: 700;
}

.event-details summary::-webkit-details-marker { display: none; }
.event-details summary > span:first-child { gap: 7px; }
.event-details[open] .details-chevron { transform: rotate(180deg); }
.details-chevron { color: var(--muted); transition: transform 160ms ease; }

.event-log {
  max-height: 360px;
  overflow-y: auto;
  display: grid;
  gap: 10px;
  margin-top: 14px;
  padding-right: 4px;
}

.event-row {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  color: var(--muted-2);
}

.event-row.is-done > svg { color: var(--success); }
.event-row.is-error > svg { color: var(--danger); }

.event-row-title {
  justify-content: space-between;
  gap: 8px;
}

.event-row-title strong {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--text);
  font-size: 11px;
  font-weight: 650;
}

.repeat-badge {
  flex: 0 0 auto;
  border-radius: 999px;
  background: var(--panel-soft);
  color: var(--muted);
  padding: 2px 6px;
  font-size: 9px;
}

.event-row-meta {
  flex-wrap: wrap;
  gap: 4px 8px;
  margin-top: 3px;
  color: var(--muted-2);
  font-size: 9px;
}

.event-row-meta time { margin-left: auto; }

.event-empty,
.event-limit-note {
  color: var(--muted);
  font-size: 11px;
}

.event-empty {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 14px;
}

.event-limit-note {
  border-radius: 6px;
  background: var(--panel-soft);
  padding: 8px 10px;
}

@media (prefers-reduced-motion: reduce) {
  .details-chevron,
  .spin { animation: none !important; transition: none; }
}
</style>
