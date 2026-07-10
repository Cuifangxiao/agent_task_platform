<script setup lang="ts">
import { normalizeStatusState } from '../lib/conversationProjection'
import { statusStateLabel } from '../lib/progressView'
import type { PptConversationSummary } from '../types'

defineProps<{
  conversation: PptConversationSummary | null
  configHint: string
}>()
</script>

<template>
  <div class="chat-shell-header">
    <div>
      <span class="chat-shell-kicker">Conversation</span>
      <strong>{{ conversation ? conversation.title : '新的对话草稿' }}</strong>
      <p>
        {{
          conversation
            ? '继续查看任务；整套修改请新建对话，单页修改请使用页面卡片中的重做入口。'
            : '从一句自然语言开始，我会先帮你整理大纲。'
        }}
      </p>
    </div>
    <div class="chat-shell-badges">
      <span class="shell-badge">{{ configHint }}</span>
      <span v-if="conversation" class="shell-badge subtle">
        {{ statusStateLabel(normalizeStatusState(conversation.status)) }}
      </span>
    </div>
  </div>
</template>
