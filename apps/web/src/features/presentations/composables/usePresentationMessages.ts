import { ref } from 'vue'

import {
  maxMessageId,
  maxStepId,
  normalizeStoredMessage,
} from '../lib/conversationProjection.ts'
import {
  artifactBoolean,
  artifactString,
  extractArtifactFileName,
  extractOutlineMarkdown,
  extractStyleOptions,
  normalizeArtifactWarnings,
  outlineTitle,
  projectStreamEvent,
} from '../lib/eventProjection.ts'
import type {
  ChatMessage,
  OutlineMessage,
  PptStreamChunk,
  SlideProgress,
  SlideProgressUpdate,
  StatusMessage,
  StyleMessage,
} from '../types'

export function usePresentationMessages(scrollToBottom: (smooth?: boolean) => Promise<void>) {
  const messages = ref<ChatMessage[]>([])
  const currentStatusMessageId = ref<number | null>(null)
  let messageSeed = 0
  let stepSeed = 0

  function hydrate(storedMessages: Record<string, unknown>[]) {
    messages.value = storedMessages.map((item) => normalizeStoredMessage(item, nextMessageId))
    messageSeed = maxMessageId(messages.value)
    stepSeed = maxStepId(messages.value)
    const latestStatus = [...messages.value].reverse().find((item): item is StatusMessage => item.kind === 'status')
    currentStatusMessageId.value = latestStatus?.id ?? null
  }

  function reset() {
    currentStatusMessageId.value = null
    messages.value = []
    messageSeed = 0
    stepSeed = 0
  }

  function applyStreamChunk(data: PptStreamChunk, occurredAt = Date.now()) {
    const statusId = currentStatusMessageId.value
    if (!statusId) return

    if (data.event_type === 'text_delta' && data.channel === 'outline_markdown') {
      upsertOutlineDelta(data)
      return
    }

    const projection = projectStreamEvent(data)
    updateStatusMessage(statusId, (status) => {
      if (data.request_id) status.requestId = data.request_id
      if (projection.slideUpdate) upsertSlideProgress(status, projection.slideUpdate)
      if (data.event_type === 'validation_report') {
        for (const slide of status.slides) {
          slide.warnings = []
          if (slide.status === 'warning') slide.status = 'reviewed'
        }
      }
      for (const warningUpdate of projection.qualityUpdates) {
        upsertSlideProgress(status, warningUpdate)
      }
      upsertProgressStep(status, projection.step, occurredAt)
      if (projection.snapshot) {
        status.title = projection.snapshot.title
        status.text = projection.snapshot.text
        status.state = projection.snapshot.state
      }
    })

    if (data.event_type === 'outline') upsertOutlineMessage(data, false)
    if (data.event_type === 'style_options') upsertStyleMessage(data, false)
    if (data.event_type === 'interrupt' && data.stage === 'style_review') upsertStyleMessage(data, true)
    if (data.event_type === 'interrupt' && data.stage !== 'style_review') upsertOutlineMessage(data, true)
    if (data.event_type === 'artifact') upsertResultMessage(data)
  }
  function nextMessageId() {
    messageSeed += 1
    return messageSeed
  }

  function nextStepId() {
    stepSeed += 1
    return stepSeed
  }

  function pushMessage(message: ChatMessage) {
    messages.value = [...messages.value, message]
    void scrollToBottom()
  }

  function createStatusMessage(title: string, text: string) {
    const id = nextMessageId()
    pushMessage({
      id,
      role: 'assistant',
      kind: 'status',
      title,
      text,
      state: 'running',
      steps: [],
      slides: [],
    })
    currentStatusMessageId.value = id
    return id
  }

  function updateStatusMessage(id: number, updater: (message: StatusMessage) => void) {
    const index = messages.value.findIndex((item) => item.id === id && item.kind === 'status')
    if (index < 0) {
      return
    }
    const message = messages.value[index]
    if (message.kind !== 'status') {
      return
    }
    updater(message)
    messages.value = [...messages.value]
    void scrollToBottom()
  }

  function upsertSlideProgress(status: StatusMessage, update: SlideProgressUpdate) {
    let slide = status.slides.find((item) => item.index === update.index)
    if (!slide) {
      slide = {
        index: update.index,
        title: update.title || `Slide ${update.index}`,
        keyMessage: '',
        layout: 'content',
        component: '',
        bullets: [],
        visualSuggestion: '',
        speakerNotes: '',
        previewPng: '',
        previewHtml: '',
        previewSvg: '',
        previewThumb: '',
        version: 0,
        status: 'pending',
        warnings: [],
      }
      status.slides.push(slide)
    }

    if (update.title !== undefined) {
      slide.title = update.title || slide.title
    }
    if (update.keyMessage !== undefined) {
      slide.keyMessage = update.keyMessage
    }
    if (update.layout !== undefined) {
      slide.layout = update.layout || slide.layout
    }
    if (update.component !== undefined) {
      slide.component = update.component || slide.component
    }
    if (update.bullets !== undefined) {
      slide.bullets = update.bullets
    }
    if (update.visualSuggestion !== undefined) {
      slide.visualSuggestion = update.visualSuggestion
    }
    if (update.speakerNotes !== undefined) {
      slide.speakerNotes = update.speakerNotes
    }
    if (update.previewPng !== undefined) {
      slide.previewPng = update.previewPng
    }
    if (update.previewHtml !== undefined) {
      slide.previewHtml = update.previewHtml
    }
    if (update.previewSvg !== undefined) {
      slide.previewSvg = update.previewSvg
    }
    if (update.previewThumb !== undefined) {
      slide.previewThumb = update.previewThumb
    }
    if (update.version !== undefined) {
      slide.version = update.version
    }
    if (update.status !== undefined && update.status !== 'pending') {
      slide.status = update.status
    }
    if (update.warnings !== undefined) {
      slide.warnings = update.replaceWarnings
        ? [...new Set(update.warnings)]
        : [...new Set([...slide.warnings, ...update.warnings])]
      if (slide.warnings.length && slide.status !== 'error') {
        slide.status = 'warning'
      }
    }

    status.slides.sort((left, right) => left.index - right.index)
  }

  function upsertProgressStep(status: StatusMessage, incoming: Omit<StatusMessage['steps'][number], 'id' | 'startedAt' | 'completedAt'>, occurredAt: number) {
    const previous = status.steps.at(-1)
    const previousIsRunning = previous?.state === 'running' && !previous.completedAt
    const sameStage = Boolean(previous && previous.stage === incoming.stage && previous.label === incoming.label)

    if (!previous || !previousIsRunning || !sameStage) {
      if (previousIsRunning) {
        previous.completedAt = occurredAt
        previous.state = 'done'
        previous.tone = 'done'
      }
      status.steps.push({
        id: nextStepId(),
        ...incoming,
        startedAt: occurredAt,
        ...(incoming.state === 'running' ? {} : { completedAt: occurredAt }),
      })
      return
    }

    previous.label = incoming.label
    previous.message = incoming.message
    previous.tone = incoming.tone
    previous.state = incoming.state
    if (incoming.state !== 'running') previous.completedAt = occurredAt
  }

  function finishRunningProgressStep(status: StatusMessage, occurredAt: number, state: 'done' | 'error') {
    const current = status.steps.at(-1)
    if (!current || current.state !== 'running' || current.completedAt) return
    current.completedAt = occurredAt
    current.state = state
    current.tone = state === 'error' ? 'error' : 'done'
  }

  function updateOutlineMessage(id: number, updater: (message: OutlineMessage) => void, shouldScroll = true) {
    const index = messages.value.findIndex((item) => item.id === id && item.kind === 'outline')
    if (index < 0) {
      return
    }
    const message = messages.value[index]
    if (message.kind !== 'outline') {
      return
    }
    updater(message)
    messages.value = [...messages.value]
    if (shouldScroll) void scrollToBottom()
  }

  function updateStyleMessage(id: number, updater: (message: StyleMessage) => void) {
    const index = messages.value.findIndex((item) => item.id === id && item.kind === 'style')
    if (index < 0) {
      return
    }
    const message = messages.value[index]
    if (message.kind !== 'style') {
      return
    }
    updater(message)
    messages.value = [...messages.value]
    void scrollToBottom()
  }

  function markCurrentRunAsStopped(message: string) {
    const statusId = currentStatusMessageId.value
    if (!statusId) {
      return
    }
    updateStatusMessage(statusId, (status) => {
      const occurredAt = Date.now()
      finishRunningProgressStep(status, occurredAt, 'error')
      status.state = 'cancelled'
      status.title = '已取消'
      status.text = message
      status.steps.push({
        id: nextStepId(),
        label: '已停止',
        message,
        stage: 'error',
        tone: 'error',
        state: 'error',
        startedAt: occurredAt,
        completedAt: occurredAt,
      })
    })
  }

  function markCurrentRunAsFailed(message: string) {
    const statusId = currentStatusMessageId.value
    if (!statusId) {
      return
    }
    updateStatusMessage(statusId, (status) => {
      const occurredAt = Date.now()
      finishRunningProgressStep(status, occurredAt, 'error')
      status.state = 'error'
      status.title = '生成失败'
      status.text = message
      status.steps.push({
        id: nextStepId(),
        label: '错误',
        message,
        stage: 'error',
        tone: 'error',
        state: 'error',
        startedAt: occurredAt,
        completedAt: occurredAt,
      })
    })
  }

  function upsertOutlineDelta(data: PptStreamChunk) {
    const requestId = data.request_id?.trim()
    const delta = data.text_delta ?? ''
    if (!requestId || !delta) {
      return
    }

    const existing = messages.value.find((item) => item.kind === 'outline' && item.requestId === requestId)
    if (existing && existing.kind === 'outline') {
      updateOutlineMessage(existing.id, (message) => {
        message.outlineMarkdown += delta
        message.title = outlineTitle(message.outlineMarkdown)
      })
      return
    }

    pushMessage({
      id: nextMessageId(),
      role: 'assistant',
      kind: 'outline',
      title: outlineTitle(delta),
      text: '正在生成 Markdown 大纲，确认后继续生成完整 PPT。',
      requestId,
      outlineMarkdown: delta,
      awaitingConfirm: false,
      showEditor: false,
    })
  }

  function upsertOutlineMessage(data: PptStreamChunk, awaitingConfirm: boolean) {
    const requestId = data.request_id?.trim()
    if (!requestId) {
      return
    }

    const outlineMarkdown = extractOutlineMarkdown(data)
    const existing = messages.value.find((item) => item.kind === 'outline' && item.requestId === requestId)

    if (existing && existing.kind === 'outline') {
      updateOutlineMessage(existing.id, (message) => {
        message.title = outlineTitle(outlineMarkdown || message.outlineMarkdown)
        message.text = '我先给你一版大纲，确认后继续生成完整 PPT。'
        if (outlineMarkdown) {
          message.outlineMarkdown = outlineMarkdown
        }
        message.title = outlineTitle(message.outlineMarkdown)
        message.awaitingConfirm = awaitingConfirm
      })
      return
    }

    pushMessage({
      id: nextMessageId(),
      role: 'assistant',
      kind: 'outline',
      title: outlineTitle(outlineMarkdown),
      text: '我先给你一版大纲，确认后继续生成完整 PPT。',
      requestId,
      outlineMarkdown,
      awaitingConfirm,
      showEditor: false,
    })
  }

  function upsertStyleMessage(data: PptStreamChunk, awaitingConfirm: boolean) {
    const requestId = data.request_id?.trim()
    if (!requestId) {
      return
    }
    const options = extractStyleOptions(data)
    const existing = messages.value.find((item) => item.kind === 'style' && item.requestId === requestId)
    if (existing && existing.kind === 'style') {
      updateStyleMessage(existing.id, (message) => {
        if (options.length) {
          message.options = options
        }
        message.awaitingConfirm = awaitingConfirm
      })
      return
    }
    pushMessage({
      id: nextMessageId(),
      role: 'assistant',
      kind: 'style',
      title: '选择 PPT 风格',
      text: '我准备了两套视觉方向，选择后继续生成页面。',
      requestId,
      options,
      awaitingConfirm,
    })
  }

  function upsertResultMessage(data: PptStreamChunk) {
    const requestId = data.request_id?.trim()
    if (!requestId) {
      return
    }

    const existingIndex = messages.value.findIndex((item) => item.kind === 'result' && item.requestId === requestId)
    const fileName = extractArtifactFileName(data) || `deck-${requestId}.pptx`
    const warnings = normalizeArtifactWarnings(data)
    const degraded = artifactBoolean(data, 'degraded')
    const qualityStatus = artifactString(data, 'quality_status')
    if (existingIndex >= 0) {
      const existing = messages.value[existingIndex]
      if (existing.kind !== 'result') return
      const updated = { ...existing, fileName, warnings, degraded, qualityStatus }
      messages.value = [
        ...messages.value.slice(0, existingIndex),
        ...messages.value.slice(existingIndex + 1),
        updated,
      ]
      void scrollToBottom()
      return
    }

    pushMessage({
      id: nextMessageId(),
      role: 'assistant',
      kind: 'result',
      title: 'PPT 已准备好',
      text: '整套内容已经生成完成，你可以直接下载这份 PPT。',
      requestId,
      fileName,
      warnings,
      degraded,
      qualityStatus,
    })
  }
  return {
    applyStreamChunk,
    createStatusMessage,
    currentStatusMessageId,
    hydrate,
    markCurrentRunAsFailed,
    markCurrentRunAsStopped,
    messages,
    nextMessageId,
    nextStepId,
    pushMessage,
    reset,
    updateOutlineMessage,
    updateStatusMessage,
    updateStyleMessage,
  }
}
