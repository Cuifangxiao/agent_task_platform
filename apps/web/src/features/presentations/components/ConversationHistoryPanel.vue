<script setup lang="ts">
import { FileClock, Loader2, MessageSquarePlus, Trash2 } from '@lucide/vue'

import { normalizeStatusState } from '../lib/conversationProjection'
import { formatHistoryTime } from '../lib/presentationUtils'
import { statusStateLabel } from '../lib/progressView'
import type { PptConversationSummary } from '../types'

defineProps<{
  conversations: PptConversationSummary[]
  currentConversationId: number | null
  loading: boolean
  disabled: boolean
  deletingConversationId: number | null
}>()

defineEmits<{
  newConversation: []
  select: [id: number]
  deleteConversation: [conversation: PptConversationSummary]
}>()
</script>

<template>
  <aside class="history-panel">
    <div class="history-panel-head">
      <div class="history-panel-copy">
        <span class="history-panel-kicker">任务记录</span>
        <strong>历史对话</strong>
        <p>继续之前的任务，或创建一条新需求。</p>
      </div>
      <button class="btn primary soft" type="button" :disabled="disabled" @click="$emit('newConversation')">
        <MessageSquarePlus :size="16" />
        新对话
      </button>
    </div>

    <div v-if="loading" class="history-empty">
      <Loader2 class="spin" :size="18" />
      <span>正在加载历史记录…</span>
    </div>
    <div v-else-if="conversations.length === 0" class="history-empty">
      <FileClock :size="18" />
      <span>还没有历史记录，先发起一条新的 PPT 需求吧。</span>
    </div>
    <div v-else class="history-list">
      <div
        v-for="conversation in conversations"
        :key="conversation.id"
        class="history-item-wrap"
      >
        <button
          type="button"
          class="history-item"
          :class="{ active: currentConversationId === conversation.id }"
          :disabled="disabled || deletingConversationId !== null"
          @click="$emit('select', conversation.id)"
        >
          <div class="history-item-head">
            <strong>{{ conversation.title }}</strong>
            <span :class="['status-pill', `is-${conversation.status}`]">
              {{ statusStateLabel(normalizeStatusState(conversation.status)) }}
            </span>
          </div>
          <p>{{ conversation.agent_config_name }}</p>
          <small>{{ formatHistoryTime(conversation.updated_at) }}</small>
        </button>
        <button
          type="button"
          class="history-delete"
          :disabled="disabled || deletingConversationId !== null"
          :aria-label="`删除会话：${conversation.title}`"
          title="删除会话"
          @click="$emit('deleteConversation', conversation)"
        >
          <Loader2 v-if="deletingConversationId === conversation.id" class="spin" :size="15" />
          <Trash2 v-else :size="15" />
        </button>
      </div>
    </div>
  </aside>
</template>
