<script setup lang="ts">
import { CircleCheck, CircleX, Clock3, Loader2 } from '@lucide/vue'

import { eventLabel, isCancelledTaskEvent, progressLabel, stageLabel } from '../lib/taskProjection'
import { formatDateTime } from '../lib/presentationUtils'
import type { PptAgentEvent } from '../types'

defineProps<{ events: PptAgentEvent[] }>()

function eventClass(event: PptAgentEvent) {
  if (isCancelledTaskEvent(event)) return 'cancelled'
  if (event.type === 'TASK_FAILED') return 'error'
  if (event.type === 'TASK_COMPLETED' || event.type === 'STEP_COMPLETED' || event.type === 'ARTIFACT_READY') return 'done'
  return 'running'
}

function timelineEventLabel(event: PptAgentEvent) {
  return isCancelledTaskEvent(event) ? '任务已取消' : eventLabel(event.type)
}
</script>

<template>
  <section class="timeline task-timeline" aria-labelledby="task-timeline-title">
    <div class="panel-head">
      <div>
        <span class="chat-shell-kicker">Agent events</span>
        <h3 id="task-timeline-title">生成事件</h3>
      </div>
      <span class="chat-note">{{ events.length }} 条</span>
    </div>

    <div v-if="events.length === 0" class="timeline-empty">
      <Clock3 :size="18" />
      <p>任务创建后，Python 发送的事件会依次显示在这里。</p>
    </div>

    <ol v-else>
      <li v-for="event in events" :key="event.sequence" :class="eventClass(event)">
        <CircleCheck v-if="eventClass(event) === 'done'" :size="18" />
        <CircleX v-else-if="eventClass(event) === 'error' || eventClass(event) === 'cancelled'" :size="18" />
        <Loader2 v-else class="spin" :size="18" />
        <div class="task-event-copy">
          <div class="event-title">
            <span>{{ stageLabel(event.stage) }}</span>
            <small>#{{ event.sequence }}</small>
          </div>
          <p>{{ event.message || eventLabel(event.type) }}</p>
          <div class="task-event-meta">
            <span>{{ timelineEventLabel(event) }}</span>
            <span v-if="progressLabel(event)">{{ progressLabel(event) }}</span>
            <time v-if="event.created_at">{{ formatDateTime(event.created_at) }}</time>
          </div>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.task-timeline {
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}

.task-timeline h3 {
  margin-top: 4px;
  color: var(--text);
  font-size: 16px;
}

.task-event-copy {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.task-event-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  color: var(--muted-2);
  font-size: 11px;
}

.task-event-meta time {
  margin-left: auto;
}
</style>
