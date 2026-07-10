import { onBeforeUnmount, ref } from 'vue'

import type { PresentationApi } from '../api'
import { normalizeStreamChunk } from '../lib/presentationUtils'
import { eventSequenceDecision, isActivePresentationJob } from '../lib/workflow'
import type {
  OutlineMessage,
  PptDeckJob,
  PptGeneratePayload,
  PptStreamChunk,
  SlideProgress,
  StyleMessage,
  StyleOption,
} from '../types'
import type { ParsedSseEvent } from '@/shared/api/sse'

interface MessageCommands {
  createStatusMessage: (title: string, text: string) => number
  markCurrentRunAsFailed: (message: string) => void
  markCurrentRunAsStopped: (message: string) => void
  nextMessageId: () => number
  pushMessage: (message: {
    id: number
    role: 'user'
    kind: 'user'
    text: string
  }) => void
  updateOutlineMessage: (id: number, updater: (message: OutlineMessage) => void) => void
  updateStyleMessage: (id: number, updater: (message: StyleMessage) => void) => void
}

interface JobControllerOptions {
  api: PresentationApi
  messages: MessageCommands
  onEvent: (event: ParsedSseEvent) => void
  syncConversation: () => Promise<void>
  styleFeedback: Record<number, string>
  slideFeedback: Record<number, string>
}

export function usePresentationJobController(options: JobControllerOptions) {
  const abortController = ref<AbortController | null>(null)
  const currentJobId = ref('')
  const error = ref('')
  const lastEventSequence = ref(0)
  const running = ref(false)
  const seenEventSequences = new Set<number>()

  onBeforeUnmount(() => abortController.value?.abort())

  function reset() {
    abortController.value?.abort()
    abortController.value = null
    currentJobId.value = ''
    lastEventSequence.value = 0
    running.value = false
    seenEventSequences.clear()
    error.value = ''
  }

  function restore(job: PptDeckJob | undefined) {
    currentJobId.value = job?.job_id ?? ''
    lastEventSequence.value = job?.last_event_sequence ?? 0
    seenEventSequences.clear()
    running.value = isActivePresentationJob(job)
    if (job && running.value) {
      void follow(job.job_id, job.last_event_sequence)
    }
  }

  function acceptEvent(event: ParsedSseEvent): PptStreamChunk | null {
    const data = normalizeStreamChunk(event.data)
    const sequence = Number(event.id ?? data.sequence ?? 0)
    if (sequence > 0) {
      const decision = eventSequenceDecision(lastEventSequence.value, seenEventSequences, sequence)
      if (!decision.accept) return null
      seenEventSequences.add(sequence)
      lastEventSequence.value = decision.last
    }
    if (data.job_id) currentJobId.value = data.job_id
    return data
  }

  async function create(payload: PptGeneratePayload, onCreated: (job: PptDeckJob) => void | Promise<void>) {
    error.value = ''
    running.value = true
    try {
      const job = await options.api.createJob(payload)
      currentJobId.value = job.job_id
      lastEventSequence.value = 0
      seenEventSequences.clear()
      await onCreated(job)
      await follow(job.job_id, 0)
      return job
    } catch (cause) {
      handleFailure(cause)
      return null
    } finally {
      await options.syncConversation()
    }
  }

  async function follow(jobId: string, afterSequence: number) {
    if (!jobId || abortController.value) return
    const controller = new AbortController()
    abortController.value = controller
    running.value = true
    try {
      await options.api.subscribeToJob(jobId, options.onEvent, controller.signal, afterSequence)
    } catch (cause) {
      if (!isAbort(cause)) {
        const job = await options.api.getJob(jobId).catch(() => null)
        if (isActivePresentationJob(job)) {
          error.value = '进度连接已断开，后台任务仍在运行，正在自动恢复。'
          window.setTimeout(() => void follow(jobId, lastEventSequence.value), 750)
        } else if (job?.status === 'failed') {
          options.messages.markCurrentRunAsFailed(job.error_summary || '生成失败。')
        }
      }
    } finally {
      if (abortController.value === controller) abortController.value = null
      const job = await options.api.getJob(jobId).catch(() => null)
      running.value = isActivePresentationJob(job)
      if (job?.status === 'cancelled') {
        options.messages.markCurrentRunAsStopped('任务已取消。')
      }
    }
  }

  async function approveOutline(message: OutlineMessage) {
    error.value = ''
    const outlineMarkdown = message.outlineMarkdown.trim()
    if (!outlineMarkdown) {
      error.value = '请先补充大纲内容。'
      options.messages.updateOutlineMessage(message.id, (draft) => { draft.showEditor = true })
      return
    }
    options.messages.updateOutlineMessage(message.id, (draft) => { draft.awaitingConfirm = false })
    options.messages.pushMessage({
      id: options.messages.nextMessageId(), role: 'user', kind: 'user',
      text: '好的，按这个大纲继续生成完整 PPT。',
    })
    options.messages.createStatusMessage('继续生成 PPT', '正在根据确认后的大纲展开每一页内容。')
    await runAction({ action: 'approve_outline', outline_markdown: outlineMarkdown })
  }

  async function regenerateOutline(message: OutlineMessage) {
    if (!currentJobId.value) {
      error.value = '缺少 job_id，请刷新对话后重试。'
      return
    }
    options.messages.updateOutlineMessage(message.id, (draft) => { draft.awaitingConfirm = false })
    options.messages.createStatusMessage('重新生成大纲', '正在重新规划叙事结构和页面重点。')
    await runAction({ action: 'regenerate_outline' })
  }

  async function chooseStyle(message: StyleMessage, option: StyleOption) {
    error.value = ''
    options.messages.updateStyleMessage(message.id, (draft) => { draft.awaitingConfirm = false })
    const feedback = options.styleFeedback[message.id]?.trim() ?? ''
    options.messages.pushMessage({
      id: options.messages.nextMessageId(), role: 'user', kind: 'user',
      text: `选择风格：${option.name}${feedback ? `；调整意见：${feedback}` : ''}`,
    })
    options.messages.createStatusMessage('生成页面预览', '正在按选定风格生成每一页的 HTML 预览。')
    await runAction({ action: 'choose_style', payload: { style_id: option.styleId, feedback } })
  }

  async function regenerateSlide(slide: SlideProgress) {
    if (!currentJobId.value) {
      error.value = '缺少 job_id，请刷新对话后重试。'
      return
    }
    const feedback = options.slideFeedback[slide.index]?.trim() ?? ''
    if (!feedback) {
      error.value = `请先填写第 ${slide.index} 页的修改意见。`
      return
    }
    error.value = ''
    options.messages.createStatusMessage('重做单页', `正在按反馈重做第 ${slide.index} 页。`)
    await runAction({ action: 'regenerate_slide', payload: { index: slide.index, feedback } })
  }

  async function cancel() {
    if (!currentJobId.value) {
      abortController.value?.abort()
      return
    }
    try {
      await options.api.cancelJob(currentJobId.value)
      abortController.value?.abort()
      options.messages.markCurrentRunAsStopped('任务已取消。')
      running.value = false
    } catch (cause) {
      error.value = errorMessage(cause)
    }
  }

  async function runAction(payload: Parameters<PresentationApi['submitJobAction']>[1]) {
    if (!currentJobId.value) {
      error.value = '缺少可恢复的 job_id，请刷新对话后重试。'
      options.messages.markCurrentRunAsFailed(error.value)
      return
    }
    const after = lastEventSequence.value
    running.value = true
    try {
      await options.api.submitJobAction(currentJobId.value, payload)
      await follow(currentJobId.value, after)
    } catch (cause) {
      handleFailure(cause)
    } finally {
      await options.syncConversation()
    }
  }

  function handleFailure(cause: unknown) {
    if (isAbort(cause)) return
    const message = errorMessage(cause)
    error.value = message
    options.messages.markCurrentRunAsFailed(message)
  }

  return {
    acceptEvent,
    approveOutline,
    cancel,
    create,
    currentJobId,
    error,
    follow,
    lastEventSequence,
    regenerateOutline,
    regenerateSlide,
    reset,
    restore,
    running,
    chooseStyle,
  }
}

function isAbort(cause: unknown) {
  return cause instanceof DOMException && cause.name === 'AbortError'
}

function errorMessage(cause: unknown) {
  return cause instanceof Error ? cause.message : String(cause)
}
