import type { ProgressStep, PptStreamChunk, SlideProgressUpdate, StatusMessage, StyleOption } from '../types'
import { normalizeSlideStatus, normalizeStringList, normalizeStyleOptions } from './conversationProjection.ts'
import { normalizeProgressStage } from './progressView.ts'

export interface StreamEventProjection {
  slideUpdate: SlideProgressUpdate | null
  qualityUpdates: SlideProgressUpdate[]
  snapshot: { title: string; text: string; state: StatusMessage['state'] } | null
  step: Omit<ProgressStep, 'id'>
}

export function projectStreamEvent(data: PptStreamChunk): StreamEventProjection {
  const state = progressStepState(data)
  return {
    slideUpdate: slideProgressFromChunk(data),
    qualityUpdates: qualityWarningUpdates(data),
    snapshot: describeChunk(data),
    step: {
      label: eventStepLabel(data),
      message: displayStepMessage(data),
      stage: normalizeProgressStage(data.stage || data.event_type || 'progress'),
      tone: state === 'error' ? 'error' : state === 'done' ? 'done' : 'normal',
      state,
    },
  }
}

export function progressStepState(data: PptStreamChunk): StreamEventProjection['step']['state'] {
  if (data.error || data.stage_status === 'error') return 'error'
  if (data.event_type === 'interrupt' || data.stage_status === 'waiting') return 'waiting'
  if (data.stage_status === 'pending') return 'pending'
  if (data.stage_status === 'done' || data.done) return 'done'
  return 'running'
}
export function slideProgressFromChunk(data: PptStreamChunk): SlideProgressUpdate | null {
  const trackedEvents = new Set([
    'slide_started',
    'slide_spec_ready',
    'slide_preview',
    'slide_ready',
    'slide_reviewed',
    'stage_progress',
  ])
  if (!trackedEvents.has(data.event_type ?? '')) {
    return null
  }
  if (data.event_type === 'stage_progress' && !['slides', 'writer', 'designer', 'repair'].includes(data.stage ?? '')) {
    return null
  }
  if (!data.payload || typeof data.payload !== 'object') {
    return null
  }

  const payload = data.payload as Record<string, unknown>
  const index = Number(payload.index)
  if (!Number.isFinite(index) || index <= 0) {
    return null
  }

  const warnings = normalizeStringList(payload.warnings)
  if (typeof payload.warning === 'string' && payload.warning.trim()) {
    warnings.push(payload.warning.trim())
  }

  const statusFromEvent =
    data.event_type === 'slide_started'
      ? 'running'
      : data.event_type === 'slide_spec_ready'
        ? 'running'
        : data.event_type === 'slide_preview' || data.event_type === 'slide_ready'
        ? 'ready'
        : data.event_type === 'slide_reviewed' && warnings.length > 0
          ? 'warning'
          : undefined

  return {
    index,
    title: typeof payload.title === 'string' ? payload.title : undefined,
    keyMessage: typeof payload.key_message === 'string' ? payload.key_message : undefined,
    layout: typeof payload.layout === 'string' ? payload.layout : undefined,
    component: typeof payload.component === 'string' ? payload.component : undefined,
    bullets: Array.isArray(payload.bullets) ? normalizeStringList(payload.bullets) : undefined,
    visualSuggestion: typeof payload.visual_suggestion === 'string' ? payload.visual_suggestion : undefined,
    speakerNotes: typeof payload.speaker_notes === 'string' ? payload.speaker_notes : undefined,
    previewPng: typeof payload.png === 'string' ? payload.png : undefined,
    previewHtml: typeof payload.html === 'string' ? payload.html : undefined,
    previewSvg: typeof payload.svg === 'string' ? payload.svg : undefined,
    previewThumb: typeof payload.thumb_b64 === 'string' ? payload.thumb_b64 : undefined,
    version: Number.isFinite(Number(payload.version)) ? Number(payload.version) : undefined,
    status: normalizeSlideStatus(payload.status ?? statusFromEvent),
    warnings,
  }
}

export function qualityWarningUpdates(data: PptStreamChunk): SlideProgressUpdate[] {
  if (data.event_type !== 'validation_report' || !data.payload || typeof data.payload !== 'object') {
    return []
  }
  const issues = (data.payload as Record<string, unknown>).issues
  if (!Array.isArray(issues)) return []
  const byPage = new Map<number, string[]>()
  for (const raw of issues) {
    if (!raw || typeof raw !== 'object') continue
    const issue = raw as Record<string, unknown>
    const page = Number(issue.page)
    const description = String(issue.description ?? '').trim()
    if (!Number.isFinite(page) || page <= 0 || !description) continue
    const current = byPage.get(page) ?? []
    current.push(description)
    byPage.set(page, current)
  }
  return [...byPage.entries()].map(([index, warnings]) => ({
    index,
    warnings: [...new Set(warnings)],
    replaceWarnings: true,
    status: 'warning',
  }))
}

export function normalizeArtifactWarnings(data: PptStreamChunk) {
  if (!data.payload || typeof data.payload !== 'object') return []
  const payload = data.payload as Record<string, unknown>
  const warnings = normalizeStringList(payload.warnings)
  if (Array.isArray(payload.issues)) {
    for (const raw of payload.issues) {
      if (!raw || typeof raw !== 'object') continue
      const issue = raw as Record<string, unknown>
      const description = String(issue.description ?? '').trim()
      if (!description) continue
      const page = Number(issue.page)
      warnings.push(`${Number.isFinite(page) && page > 0 ? `第 ${page} 页：` : '全局：'}${description}`)
    }
  }
  return [...new Set(warnings)]
}

export function artifactBoolean(data: PptStreamChunk, key: string) {
  return Boolean(data.payload && typeof data.payload === 'object' && (data.payload as Record<string, unknown>)[key])
}

export function artifactString(data: PptStreamChunk, key: string) {
  return data.payload && typeof data.payload === 'object' ? String((data.payload as Record<string, unknown>)[key] ?? '') : ''
}

export function extractOutlineMarkdown(data: PptStreamChunk) {
  if (typeof data.outline_markdown === 'string' && data.outline_markdown.trim()) {
    return data.outline_markdown
  }
  if (typeof data.content === 'string' && data.content.trim()) {
    return data.content
  }
  if (!data.payload || typeof data.payload !== 'object') {
    return ''
  }
  const payload = data.payload as Record<string, unknown>
  if (typeof payload.outline_markdown === 'string') {
    return payload.outline_markdown
  }
  if (typeof payload.outlineMarkdown === 'string') {
    return payload.outlineMarkdown
  }
  if (typeof payload.outline === 'string') {
    return payload.outline
  }
  return ''
}

export function extractStyleOptions(data: PptStreamChunk): StyleOption[] {
  if (!data.payload || typeof data.payload !== 'object') {
    return []
  }
  const payload = data.payload as Record<string, unknown>
  return normalizeStyleOptions(payload.options ?? payload.style_options)
}

export function outlineTitle(markdown: string) {
  const heading = markdown
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line.startsWith('# '))
  return heading ? heading.replace(/^#\s+/, '').trim() || 'PPT 大纲' : 'PPT 大纲'
}

export function extractArtifactFileName(data: PptStreamChunk) {
  if (!data.payload || typeof data.payload !== 'object') {
    return ''
  }
  const payload = data.payload as Record<string, unknown>
  return typeof payload.filename === 'string' ? payload.filename : ''
}

export function describeChunk(data: PptStreamChunk) {
  if (data.error) {
    return {
      title: '生成失败',
      text: data.error,
      state: 'error' as const,
    }
  }

  if (data.event_type === 'interrupt') {
    if (data.stage === 'style_review') {
      return {
        title: '等待选择风格',
        text: '静态模板封面已经准备好，选择后再进入正式页面设计。',
        state: 'waiting' as const,
      }
    }
    return {
      title: '等待确认大纲',
      text: '我已经整理好一版大纲，确认后就继续生成完整 PPT。',
      state: 'waiting' as const,
    }
  }

  if (data.event_type === 'artifact' || data.event_type === 'done') {
    return {
      title: 'PPT 已生成',
      text: 'PPT 已经生成完成，可以直接下载。',
      state: 'done' as const,
    }
  }

  switch (data.stage) {
    case 'brief':
      return {
        title: '正在理解需求',
        text: '我在整理你的目标和上下文信息。',
        state: 'running' as const,
      }
    case 'outline':
      return {
        title: '正在规划大纲',
        text: '我在梳理 PPT 结构和每一页的重点。',
        state: 'running' as const,
      }
    case 'outline_review':
      return {
        title: '等待确认大纲',
        text: '大纲已经准备好，确认后我继续生成页面内容。',
        state: data.done ? ('waiting' as const) : ('running' as const),
      }
    case 'style':
      return {
        title: '正在读取模板目录',
        text: '我在按内容筛选静态模板封面，不会在这里调用 Designer 实时绘制。',
        state: 'running' as const,
      }
    case 'style_review':
      return {
        title: '等待选择风格',
        text: '请选择一套视觉方向继续。',
        state: data.done ? ('waiting' as const) : ('running' as const),
      }
    case 'design_lock':
      return {
        title: '正在冻结设计规范',
        text: '正在锁定画布、色板、字体、页面节奏和 SVG 导出约束。',
        state: 'running' as const,
      }
    case 'slides':
    case 'writer':
      return {
        title: '正在展开页面',
        text: '我在把大纲扩展成每一页的具体内容。',
        state: 'running' as const,
      }
    case 'design_slides':
    case 'designer':
      return {
        title: '正在生成页面预览',
        text: '我在把结构化内容渲染成 HTML 与 PNG 预览。',
        state: 'running' as const,
      }
    case 'validate':
      return {
        title: '正在校验页面',
        text: '我在检查占位、密度和页面结构。',
        state: 'running' as const,
      }
    case 'coherence':
      return {
        title: '正在统一全局表达',
        text: '我在检查跨页连贯性。',
        state: 'running' as const,
      }
    case 'review':
    case 'review_vlm':
      return {
        title: '正在检查内容',
        text: '我在检查逻辑、重复信息和内容完整性。',
        state: 'running' as const,
      }
    case 'render':
    case 'export':
      return {
        title: '正在导出 PPT',
        text: '正在把内容渲染成可下载的 PPT 文件。',
        state: 'running' as const,
      }
    default:
      return null
  }
}

export function defaultStepMessage(data: PptStreamChunk) {
  if (data.error) {
    return data.error
  }
  return '已收到新的生成事件。'
}

export function displayStepMessage(data: PptStreamChunk) {
  const base = data.message || defaultStepMessage(data)
  if (!data.payload || typeof data.payload !== 'object') return base
  const metrics = (data.payload as Record<string, unknown>).metrics
  if (!metrics || typeof metrics !== 'object') return base
  const values = metrics as Record<string, unknown>
  const duration = Number(values.duration_ms)
  const details: string[] = []
  if (Number.isFinite(duration) && duration >= 0) details.push(`${(duration / 1000).toFixed(1)}s`)
  const model = [values.provider, values.model].filter((item) => typeof item === 'string' && item).join('/')
  if (model) details.push(model)
  const tokens = Number(values.token_usage)
  if (Number.isFinite(tokens) && tokens > 0) details.push(`${tokens} tokens`)
  const retries = Number(values.retry_count)
  if (Number.isFinite(retries) && retries > 0) details.push(`重试 ${retries}`)
  const fallbacks = Number(values.fallback_count)
  if (Number.isFinite(fallbacks) && fallbacks > 0) details.push(`降级 ${fallbacks}`)
  return details.length ? `${base} · ${details.join(' · ')}` : base
}

export function eventStepLabel(data: PptStreamChunk) {
  if (data.payload && typeof data.payload === 'object' && (data.payload as Record<string, unknown>).phase === 'parse') {
    return '附件解析'
  }
  return stageLabel(data.stage || data.event_type || 'progress')
}

export function stageLabel(stage: string) {
  switch (stage) {
    case 'design_lock':
      return '设计规范锁定'
    case 'brief':
      return '需求理解'
    case 'outline':
      return '大纲规划'
    case 'outline_review':
      return '大纲确认'
    case 'style':
      return '风格样张'
    case 'style_review':
      return '风格选择'
    case 'slides':
    case 'writer':
      return '页面展开'
    case 'design_slides':
    case 'designer':
      return '页面预览'
    case 'validate':
      return '质量校验'
    case 'coherence':
      return '连贯检查'
    case 'review':
    case 'review_vlm':
      return '质量检查'
    case 'render':
    case 'export':
      return 'PPT 导出'
    case 'artifact':
      return '下载结果'
    case 'done':
      return '完成'
    case 'error':
      return '错误'
    default:
      return '处理中'
  }
}
