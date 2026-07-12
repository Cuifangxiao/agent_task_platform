import type {
  ChatMessage,
  ProgressStep,
  SlideProgress,
  SlideProgressStatus,
  StatusMessage,
  StyleOption,
} from '../types'
import { normalizeProgressStage } from './progressView.ts'
export function normalizeStoredMessage(item: Record<string, unknown>, nextMessageId: () => number): ChatMessage {
  const id = Number(item.id ?? nextMessageId())
  const role = item.role === 'user' ? 'user' : 'assistant'
  const kind = String(item.kind ?? 'status')

  if (kind === 'user') {
    return {
      id,
      role: 'user',
      kind: 'user',
      text: String(item.text ?? ''),
      fileNames: normalizeStoredFileNames(item),
    }
  }

  if (kind === 'outline') {
    return {
      id,
      role: 'assistant',
      kind: 'outline',
      title: String(item.title ?? 'PPT 大纲'),
      text: String(item.text ?? ''),
      requestId: String(item.requestId ?? ''),
      outlineMarkdown: String(item.outlineMarkdown ?? ''),
      awaitingConfirm: Boolean(item.awaitingConfirm),
      showEditor: Boolean(item.showEditor),
    }
  }

  if (kind === 'style') {
    return {
      id,
      role: 'assistant',
      kind: 'style',
      title: String(item.title ?? '选择 PPT 风格'),
      text: String(item.text ?? ''),
      requestId: String(item.requestId ?? ''),
      options: normalizeStyleOptions(item.options),
      awaitingConfirm: Boolean(item.awaitingConfirm),
    }
  }

  if (kind === 'result') {
    return {
      id,
      role: 'assistant',
      kind: 'result',
      title: String(item.title ?? 'PPT 已准备好'),
      text: String(item.text ?? ''),
      requestId: String(item.requestId ?? ''),
      fileName: String(item.fileName ?? ''),
      warnings: normalizeStringList(item.warnings),
      degraded: Boolean(item.degraded),
      qualityStatus: String(item.qualityStatus ?? ''),
    }
  }

  return {
    id,
    role: 'assistant',
    kind: 'status',
    title: String(item.title ?? '处理中'),
    text: String(item.text ?? ''),
    requestId: typeof item.requestId === 'string' ? item.requestId : undefined,
    state: normalizeStatusState(item.state),
    steps: normalizeStoredSteps(item.steps),
    slides: normalizeStoredSlides(item.slides),
  }
}

export function normalizeStoredFileNames(item: Record<string, unknown>) {
  if (Array.isArray(item.fileNames)) {
    return item.fileNames.map((fileName) => String(fileName)).filter(Boolean)
  }
  return typeof item.fileName === 'string' && item.fileName ? [item.fileName] : []
}

export function normalizeStatusState(value: unknown): StatusMessage['state'] {
  switch (value) {
    case 'running':
    case 'waiting':
    case 'done':
    case 'error':
      return value
    case 'cancelled':
      return value
    default:
      return 'waiting'
  }
}

export function normalizeStoredSteps(value: unknown): ProgressStep[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value.map((item, index) => {
    const record = item && typeof item === 'object' ? (item as Record<string, unknown>) : {}
    const startedAt = normalizeStepTimestamp(record.startedAt)
    const completedAt = normalizeStepTimestamp(record.completedAt)
    return {
      id: Number(record.id ?? index + 1),
      label: String(record.label ?? '处理中'),
      message: String(record.message ?? ''),
      stage: typeof record.stage === 'string' ? normalizeProgressStage(record.stage) : undefined,
      tone: normalizeStepTone(record.tone),
      state: normalizeProgressStepState(record.state, record.tone, startedAt),
      ...(startedAt === undefined ? {} : { startedAt }),
      ...(completedAt === undefined ? {} : { completedAt }),
    }
  })
}

export function normalizeStepTimestamp(value: unknown) {
  const timestamp = Number(value)
  return Number.isFinite(timestamp) && timestamp >= 0 ? timestamp : undefined
}

export function normalizeProgressStepState(value: unknown, tone: unknown, startedAt: number | undefined): ProgressStep['state'] {
  switch (value) {
    case 'pending':
    case 'running':
    case 'waiting':
    case 'done':
    case 'error':
      return value
    default:
      if (tone === 'error') return 'error'
      if (tone === 'done') return 'done'
      return startedAt === undefined ? 'done' : 'running'
  }
}

export function normalizeStoredSlides(value: unknown): SlideProgress[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value
    .map((item) => {
      const record = item && typeof item === 'object' ? (item as Record<string, unknown>) : {}
      const index = Number(record.index)
      if (!Number.isFinite(index) || index <= 0) {
        return null
      }
      return {
        index,
        title: String(record.title ?? `Slide ${index}`),
        keyMessage: String(record.keyMessage ?? record.key_message ?? ''),
        layout: String(record.layout ?? 'content'),
        component: String(record.component ?? ''),
        bullets: normalizeStringList(record.bullets),
        visualSuggestion: String(record.visualSuggestion ?? record.visual_suggestion ?? ''),
        speakerNotes: String(record.speakerNotes ?? record.speaker_notes ?? ''),
        previewPng: String(record.previewPng ?? record.png ?? ''),
        previewHtml: String(record.previewHtml ?? record.html ?? ''),
        previewSvg: String(record.previewSvg ?? record.svg ?? ''),
        previewThumb: String(record.previewThumb ?? record.thumb_b64 ?? ''),
        version: Number(record.version ?? 0),
        status: normalizeSlideStatus(record.status),
        warnings: normalizeStringList(record.warnings),
      }
    })
    .filter((item): item is SlideProgress => Boolean(item))
    .sort((left, right) => left.index - right.index)
}

export function normalizeStyleOptions(value: unknown): StyleOption[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value
    .map((item) => {
      const record = item && typeof item === 'object' ? (item as Record<string, unknown>) : {}
      const styleId = String(record.style_id ?? record.styleId ?? '')
      if (!styleId) {
        return null
      }
      return {
        styleId,
        name: String(record.name ?? styleId),
        communicationMode: String(record.communication_mode ?? record.communicationMode ?? ''),
        visualStyle: String(record.visual_style ?? record.visualStyle ?? ''),
        templateId: String(record.template_id ?? record.templateId ?? ''),
        templateMode: String(record.template_mode ?? record.templateMode ?? ''),
        templateSummary: String(record.template_summary ?? record.templateSummary ?? ''),
        rationale: String(record.rationale ?? ''),
        confirmations: normalizeStringRecord(record.confirmations),
        layoutPrinciples: normalizeStringList(record.layout_principles ?? record.layoutPrinciples),
        imageStrategy: String(record.image_strategy ?? record.imageStrategy ?? ''),
        coverPng: String(record.cover_png ?? record.coverPng ?? ''),
        coverHtml: String(record.cover_html ?? record.coverHtml ?? ''),
        thumbB64: String(record.thumb_b64 ?? record.thumbB64 ?? ''),
        tokensSummary: String(record.tokens_summary ?? record.tokensSummary ?? ''),
      }
    })
    .filter((item): item is StyleOption => Boolean(item))
}

export function normalizeStepTone(value: unknown): ProgressStep['tone'] {
  return value === 'error' || value === 'done' ? value : 'normal'
}

export function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.map((item) => String(item).trim()).filter(Boolean)
}

export function normalizeStringRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {}
  }
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => [key.trim(), String(item ?? '').trim()] as const)
      .filter(([key, item]) => Boolean(key && item)),
  )
}

export function normalizeSlideStatus(value: unknown): SlideProgressStatus {
  switch (value) {
    case 'pending':
    case 'running':
    case 'ready':
    case 'reviewed':
    case 'warning':
    case 'error':
      return value
    case 'ok':
      return 'reviewed'
    default:
      return 'pending'
  }
}

export function maxMessageId(items: ChatMessage[]) {
  return items.reduce((max, item) => Math.max(max, item.id || 0), 0)
}

export function maxStepId(items: ChatMessage[]) {
  return items.reduce((max, item) => {
    if (item.kind !== 'status') {
      return max
    }
    return Math.max(max, item.steps.reduce((current, step) => Math.max(current, step.id || 0), 0))
  }, 0)
}
