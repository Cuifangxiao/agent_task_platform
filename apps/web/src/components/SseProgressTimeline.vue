<script setup lang="ts">
import { CheckCircle2, Circle, Loader2, XCircle } from '@lucide/vue'

export interface TimelineEvent {
  id: number
  event: string
  stage: string
  message: string
  sequence?: number
  tone?: 'normal' | 'error' | 'done'
}

defineProps<{
  events: TimelineEvent[]
  running: boolean
}>()
</script>

<template>
  <section class="timeline">
    <div class="timeline-head">
      <strong>生成进度</strong>
      <Loader2 v-if="running" class="spin" :size="18" />
    </div>

    <div v-if="events.length === 0" class="timeline-empty">
      <Circle :size="18" />
      <span>提交后会实时展示 outline、slide、review 和 artifact 事件。</span>
    </div>

    <ol v-else>
      <li v-for="item in events" :key="item.id" :class="item.tone">
        <CheckCircle2 v-if="item.tone === 'done'" :size="18" />
        <XCircle v-else-if="item.tone === 'error'" :size="18" />
        <Circle v-else :size="18" />
        <div>
          <div class="event-title">
            <span>{{ item.stage || item.event }}</span>
            <small v-if="item.sequence !== undefined">#{{ item.sequence }}</small>
          </div>
          <p>{{ item.message }}</p>
        </div>
      </li>
    </ol>
  </section>
</template>
