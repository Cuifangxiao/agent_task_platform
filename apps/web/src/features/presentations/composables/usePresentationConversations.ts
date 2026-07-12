import { computed, onMounted, ref, watch } from 'vue'
import type { Ref } from 'vue'

import type { PresentationApi } from '../api'
import type { AgentConfig } from '@/types'
import type { PptConversationSummary, PptDeckJob } from '../types'

interface ConversationMessages {
  hydrate: (messages: Record<string, unknown>[]) => void
  reset: () => void
}

interface ConversationJob {
  error: Ref<string>
  reset: () => void
  restore: (job: PptDeckJob | undefined) => void
  running: Ref<boolean>
}

interface ConversationOptions {
  api: PresentationApi
  configs: () => AgentConfig[]
  messages: ConversationMessages
  job: ConversationJob
  clearComposer: () => void
  clearFiles: () => void
  scrollToBottom: (smooth?: boolean) => Promise<void>
}

export function usePresentationConversations(options: ConversationOptions) {
  const conversationList = ref<PptConversationSummary[]>([])
  const currentConversationId = ref<number | null>(null)
  const historyLoading = ref(false)
  const deletingConversationId = ref<number | null>(null)
  const selectedConfigId = ref('')

  const defaultConfig = computed(() => options.configs().find((item) => item.is_default) ?? options.configs()[0] ?? null)
  const selectedConfig = computed(() =>
    options.configs().find((item) => String(item.id) === selectedConfigId.value) ?? null,
  )
  const currentConversationSummary = computed(() =>
    conversationList.value.find((item) => item.id === currentConversationId.value) ?? null,
  )
  const selectedConfigHint = computed(() => {
    if (!selectedConfig.value) return '请选择一个 PPT 配置'
    return selectedConfig.value.is_default ? `${selectedConfig.value.name} · 默认配置` : selectedConfig.value.name
  })
  const conversationCountLabel = computed(() =>
    conversationList.value.length > 0 ? `${conversationList.value.length} 条历史对话` : '暂无历史对话',
  )

  onMounted(() => {
    syncSelectedConfig()
    void refreshHistory({ selectLatest: true })
  })

  watch(options.configs, syncSelectedConfig, { immediate: true })

  function syncSelectedConfig() {
    if (!options.configs().some((item) => String(item.id) === selectedConfigId.value)) {
      selectedConfigId.value = defaultConfig.value ? String(defaultConfig.value.id) : ''
    }
  }

  function resetDraftConversation(keepComposer = true) {
    currentConversationId.value = null
    options.messages.reset()
    options.job.reset()
    if (!keepComposer) options.clearComposer()
    syncSelectedConfig()
  }

  function startNewConversation() {
    resetDraftConversation(false)
  }

  async function refreshHistory(settings: { selectLatest?: boolean; selectConversationId?: number } = {}) {
    historyLoading.value = true
    try {
      const list = await options.api.listConversations()
      conversationList.value = list
      if (settings.selectConversationId) {
        await openConversation(settings.selectConversationId)
        return
      }
      if (currentConversationId.value) {
        if (list.some((item) => item.id === currentConversationId.value)) return
        currentConversationId.value = null
      }
      if (settings.selectLatest && list[0]) await openConversation(list[0].id)
    } finally {
      historyLoading.value = false
    }
  }

  async function openConversation(id: number) {
    if (options.job.running.value && currentConversationId.value && currentConversationId.value !== id) return
    const detail = await options.api.getConversation(id)
    currentConversationId.value = detail.id
    selectedConfigId.value = String(detail.agent_config_id)
    options.messages.hydrate(detail.messages)
    options.job.error.value = ''
    options.clearFiles()
    await options.scrollToBottom(false)
    options.job.restore(detail.job)
  }

  async function deleteConversation(id: number) {
    if (options.job.running.value || deletingConversationId.value !== null) return
    deletingConversationId.value = id
    options.job.error.value = ''
    try {
      await options.api.deleteConversation(id)
      const deletingCurrentConversation = currentConversationId.value === id
      conversationList.value = conversationList.value.filter((item) => item.id !== id)
      if (!deletingCurrentConversation) return

      resetDraftConversation(false)
      const nextConversation = conversationList.value[0]
      if (nextConversation) await openConversation(nextConversation.id)
    } catch (cause) {
      options.job.error.value = cause instanceof Error ? cause.message : String(cause)
    } finally {
      deletingConversationId.value = null
    }
  }

  async function syncCurrentConversationFromServer() {
    // The active stream is already projected locally. Refresh only the history list so
    // live timing records are not replaced by the server's compact event snapshot.
    await refreshHistory()
  }

  return {
    conversationCountLabel,
    conversationList,
    currentConversationId,
    currentConversationSummary,
    deleteConversation,
    deletingConversationId,
    historyLoading,
    openConversation,
    refreshHistory,
    resetDraftConversation,
    selectedConfig,
    selectedConfigHint,
    selectedConfigId,
    startNewConversation,
    syncCurrentConversationFromServer,
  }
}
