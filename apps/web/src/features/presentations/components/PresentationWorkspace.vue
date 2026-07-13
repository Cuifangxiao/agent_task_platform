<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { Presentation } from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
import type { AgentConfig } from '@/types'

import type { PresentationApi } from '../api'
import { usePresentationAssets } from '../composables/usePresentationAssets'
import { usePresentationConversations } from '../composables/usePresentationConversations'
import { usePresentationJobController } from '../composables/usePresentationJobController'
import { usePresentationMessages } from '../composables/usePresentationMessages'
import { validateSourceFiles } from '../lib/presentationUtils'
import type { PptConversationSummary, PptGeneratePayload, PresentationMessageAction } from '../types'
import type { ParsedSseEvent } from '@/shared/api/sse'
import ConversationHeader from './ConversationHeader.vue'
import ConversationHistoryPanel from './ConversationHistoryPanel.vue'
import PresentationComposer from './PresentationComposer.vue'
import PresentationPreviewDialog from './PresentationPreviewDialog.vue'
import PresentationThread from './PresentationThread.vue'

const props = defineProps<{
  configs: AgentConfig[]
  api: PresentationApi
}>()

interface ThreadHandle {
  scrollToBottom: (smooth?: boolean) => Promise<void>
}

const composer = reactive({ text: '', files: [] as File[] })
const generationMode = ref<'quick' | 'step_by_step'>('step_by_step')
const outlineFeedback = reactive<Record<number, string>>({})
const slideFeedback = reactive<Record<number, string>>({})
const styleFeedback = reactive<Record<number, string>>({})
const threadRef = ref<ThreadHandle | null>(null)
const uploadState = ref('')

const scrollToBottom = (smooth = true) => threadRef.value?.scrollToBottom(smooth) ?? Promise.resolve()
const messageStore = usePresentationMessages(scrollToBottom)

let syncConversation = async () => undefined as void
const job = usePresentationJobController({
  api: props.api,
  messages: messageStore,
  onEvent: handleEvent,
  outlineFeedback,
  slideFeedback,
  styleFeedback,
  syncConversation: () => syncConversation(),
})

const conversations = usePresentationConversations({
  api: props.api,
  configs: () => props.configs,
  messages: messageStore,
  job,
  clearComposer,
  clearFiles,
  scrollToBottom,
})
syncConversation = conversations.syncCurrentConversationFromServer

const assets = usePresentationAssets(props.api, (message) => { job.error.value = message })
const canSubmit = computed(() =>
  Boolean(conversations.selectedConfig.value) &&
  composer.text.trim().length > 0 &&
  !job.running.value &&
  messageStore.messages.value.length === 0,
)

function handleEvent(event: ParsedSseEvent) {
  const data = job.acceptEvent(event)
  if (!data) return
  if (data.event_type === 'fact_sheet_ready') {
    uploadState.value = '附件解析完成，事实表已建立。'
  }
  if (typeof data.conversation_id === 'number' && data.conversation_id > 0) {
    conversations.currentConversationId.value = data.conversation_id
  }
  messageStore.applyStreamChunk(data)
  if (data.error) job.error.value = data.error
}

async function startGeneration() {
  job.error.value = ''
  const description = composer.text.trim()
  const config = conversations.selectedConfig.value
  if (!config) {
    job.error.value = '请先选择一个可用的 PPTAgent 配置。'
    return
  }
  if (!description) {
    job.error.value = '先描述一下你想生成的 PPT 内容。'
    return
  }
  if (messageStore.messages.value.some((item) => item.kind === 'result')) {
    job.error.value = '整套修改请先新建对话；已有 Deck 请在具体页面使用“重做此页”。'
    return
  }
  if (conversations.currentConversationId.value || messageStore.messages.value.length > 0) {
    conversations.resetDraftConversation(true)
  }

  const selectedFiles = [...composer.files]
  const fileError = validateSourceFiles(selectedFiles)
  if (fileError) {
    job.error.value = fileError
    return
  }
  const fileNames = selectedFiles.map((file) => file.name)
  messageStore.pushMessage({
    id: messageStore.nextMessageId(),
    role: 'user',
    kind: 'user',
    text: description,
    ...(fileNames.length ? { fileNames } : {}),
  })
  const statusId = messageStore.createStatusMessage(
    '正在规划大纲',
    '我先根据你的描述整理一版 PPT 大纲，稍后发给你确认。',
  )
  const payload: PptGeneratePayload = {
    agent_config_id: config.id,
    description,
    source_files: selectedFiles,
    mode: generationMode.value,
  }

  composer.text = ''
  clearFiles()
  const uploadStartedAt = Date.now()
  uploadState.value = selectedFiles.length ? '正在上传并创建后台任务…' : '正在创建后台任务…'
  try {
    await job.create(payload, (createdJob) => {
      conversations.currentConversationId.value = createdJob.conversation_id
      uploadState.value = selectedFiles.length ? '附件已上传，等待解析。' : '后台任务已创建。'
      messageStore.updateStatusMessage(statusId, (status) => {
        const completedAt = Date.now()
        status.steps.push({
          id: messageStore.nextStepId(),
          label: selectedFiles.length ? '附件上传' : '任务创建',
          message: selectedFiles.length ? '附件上传完成' : '后台任务创建完成',
          stage: 'brief',
          tone: 'done',
          state: 'done',
          startedAt: uploadStartedAt,
          completedAt,
        })
      })
    })
  } finally {
    uploadState.value = ''
  }
}

function handleMessageAction(action: PresentationMessageAction) {
  switch (action.type) {
    case 'preview':
      assets.openPreview(action.src, action.title, action.subtitle)
      break
    case 'approve-outline':
      void job.approveOutline(action.message)
      break
    case 'regenerate-outline':
      void job.regenerateOutline(action.message)
      break
    case 'choose-style':
      void job.chooseStyle(action.message, action.option)
      break
    case 'regenerate-slide':
      void job.regenerateSlide(action.slide)
      break
    case 'download':
      void assets.downloadDeck(action.requestId, action.fileName)
      break
    case 'update-outline':
      messageStore.updateOutlineMessage(action.messageId, (message) => { message.outlineMarkdown = action.value }, false)
      break
    case 'update-outline-feedback':
      outlineFeedback[action.messageId] = action.value
      break
    case 'toggle-outline-editor':
      messageStore.updateOutlineMessage(action.messageId, (message) => { message.showEditor = !message.showEditor }, false)
      break
    case 'update-style-feedback':
      styleFeedback[action.messageId] = action.value
      break
    case 'update-slide-feedback':
      slideFeedback[action.slideIndex] = action.value
      break
  }
}

function addFiles(files: File[]) {
  const nextFiles = [...composer.files, ...files]
  const error = validateSourceFiles(nextFiles)
  if (error) {
    job.error.value = error
    return
  }
  composer.files = nextFiles
  job.error.value = ''
}

function removeFile(index: number) {
  composer.files = composer.files.filter((_, currentIndex) => currentIndex !== index)
}

function clearFiles() {
  composer.files = []
}

function clearComposer() {
  composer.text = ''
  clearFiles()
}

function confirmDeleteConversation(conversation: PptConversationSummary) {
  if (job.running.value || conversations.deletingConversationId.value !== null) return
  const confirmed = window.confirm(
    `确定删除 PPT 会话“${conversation.title}”吗？\n\n聊天记录、任务记录和对应的 PPT 产物都会永久删除。`,
  )
  if (confirmed) void conversations.deleteConversation(conversation.id)
}
</script>

<template>
  <section class="page chat-page management-page">
    <header class="page-head management-head chat-page-head">
      <div class="page-title-block chat-page-copy">
        <span class="page-eyebrow">智能演示文稿</span>
        <h2>PPT 生成助手</h2>
        <p>像聊天一样描述需求，上传资料后我会先给你大纲，确认后再生成完整 PPT。</p>
      </div>
      <div v-if="props.configs.length > 0" class="chat-page-meta">
        <div class="page-meta-card"><span>当前配置</span><strong>{{ conversations.selectedConfigHint.value }}</strong></div>
        <div class="page-meta-card"><span>历史对话</span><strong>{{ conversations.conversationCountLabel.value }}</strong></div>
      </div>
    </header>

    <EmptyState
      v-if="props.configs.length === 0"
      :icon="Presentation"
      title="还没有可用的 PPT 配置"
      text="请先到 Agent 管理里创建一个 active 的 PPTAgent 配置，再回来发起生成。"
    />

    <div v-else class="chat-layout">
      <ConversationHistoryPanel
        :conversations="conversations.conversationList.value"
        :current-conversation-id="conversations.currentConversationId.value"
        :deleting-conversation-id="conversations.deletingConversationId.value"
        :loading="conversations.historyLoading.value"
        :disabled="job.running.value"
        @delete-conversation="confirmDeleteConversation"
        @new-conversation="conversations.startNewConversation"
        @select="conversations.openConversation"
      />

      <div class="chat-shell">
        <ConversationHeader
          :conversation="conversations.currentConversationSummary.value"
          :config-hint="conversations.selectedConfigHint.value"
        />
        <PresentationThread
          ref="threadRef"
          :conversation-title="conversations.currentConversationSummary.value?.title ?? null"
          :messages="messageStore.messages.value"
          :outline-feedback="outlineFeedback"
          :preview-src="assets.previewSrc"
          :running="job.running.value"
          :slide-feedback="slideFeedback"
          :style-feedback="styleFeedback"
          @action="handleMessageAction"
        />
        <PresentationComposer
          :can-submit="canSubmit"
          :config-id="conversations.selectedConfigId.value"
          :configs="props.configs"
          :error="job.error.value"
          :files="composer.files"
          :mode="generationMode"
          :running="job.running.value"
          :text="composer.text"
          :upload-state="uploadState"
          @add-files="addFiles"
          @remove-file="removeFile"
          @stop="job.cancel"
          @submit="startGeneration"
          @update-config-id="conversations.selectedConfigId.value = $event"
          @update-mode="generationMode = $event"
          @update-text="composer.text = $event"
        />
      </div>
    </div>

    <PresentationPreviewDialog :preview="assets.previewLightbox.value" @close="assets.closePreview" />
  </section>
</template>
