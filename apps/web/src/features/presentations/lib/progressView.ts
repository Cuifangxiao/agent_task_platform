import type { ProgressStep, SlideProgressStatus, StatusMessage } from '../types'

export const pipelineStages = [
  { key: 'brief', label: '理解需求', description: '整理目标、受众和上下文' },
  { key: 'outline', label: '规划大纲', description: '生成 PPT 结构和每页重点' },
  { key: 'outline_review', label: '确认大纲', description: '等待你确认或调整大纲' },
  { key: 'style', label: '筛选模板', description: '读取静态模板封面与设计约束' },
  { key: 'style_review', label: '选择模板', description: '等待选择模板方向' },
  { key: 'design_lock', label: '冻结设计规范', description: '锁定画布、色板、字体、页面节奏和 SVG 约束' },
  { key: 'writer', label: '撰写页面', description: '把大纲扩展成逐页结构化内容' },
  { key: 'designer', label: '设计页面', description: '生成权威 SVG 和 PNG 预览' },
  { key: 'validate', label: '质量校验', description: '检查版式、密度、SVG 和内容结构' },
  { key: 'repair', label: '自动修复', description: '修复校验发现的问题' },
  { key: 'review_vlm', label: '视觉复核', description: '检查视觉质量和完整性' },
  { key: 'coherence', label: '统一表达', description: '检查跨页连贯性' },
  { key: 'export', label: '导出 PPT', description: '把 SVG 转成可编辑的 PowerPoint 形状' },
  { key: 'artifact', label: '完成结果', description: '准备下载文件' },
] as const

export function normalizeProgressStage(value: string | undefined) {
  switch (value) {
    case 'brief':
    case 'outline':
    case 'outline_review':
    case 'style':
    case 'style_review':
    case 'design_lock':
    case 'writer':
    case 'designer':
    case 'validate':
    case 'repair':
    case 'coherence':
    case 'review_vlm':
    case 'export':
    case 'artifact':
      return value
    case 'slides':
      return 'writer'
    case 'design_slides':
      return 'designer'
    case 'review':
      return 'review_vlm'
    case 'render':
    case 'export_progress':
      return 'export'
    case 'done':
      return 'artifact'
    default:
      return ''
  }
}

export function latestProgressStep(message: StatusMessage) {
  return [...message.steps].reverse().find((step) => step.stage) ?? null
}

export function currentProgressLabel(message: StatusMessage) {
  const latest = latestProgressStep(message)
  return latest?.message || message.text
}

export function currentProgressStageLabel(message: StatusMessage) {
  const latest = latestProgressStep(message)
  if (!latest?.stage) {
    return message.state === 'waiting' ? '等待下一步' : '准备开始'
  }
  return pipelineStages.find((stage) => stage.key === latest.stage)?.label ?? latest.label
}

export function progressStepIsRunning(step: ProgressStep) {
  return step.state === 'running' && !step.completedAt && typeof step.startedAt === 'number'
}

export function progressStepDurationMs(step: ProgressStep, now = Date.now()) {
  if (typeof step.startedAt !== 'number') return null
  const end = step.completedAt ?? (progressStepIsRunning(step) ? now : step.startedAt)
  return Math.max(0, end - step.startedAt)
}

export function formatProgressDuration(durationMs: number | null) {
  if (durationMs === null) return '—'
  const seconds = Math.floor(durationMs / 1000)
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainingSeconds = seconds % 60
  if (hours > 0) {
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`
  }
  return `${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`
}

export function progressStepStateLabel(step: ProgressStep) {
  switch (step.state) {
    case 'running':
      return '计时中'
    case 'waiting':
      return '等待确认'
    case 'done':
      return '已完成'
    case 'error':
      return '已中断'
    default:
      return '未开始'
  }
}

export function statusStateLabel(state: StatusMessage['state']) {
  switch (state) {
    case 'running':
      return '进行中'
    case 'waiting':
      return '待确认'
    case 'done':
      return '已完成'
    case 'error':
      return '异常'
    case 'cancelled':
      return '已取消'
    default:
      return ''
  }
}

export function slideStatusLabel(status: SlideProgressStatus) {
  switch (status) {
    case 'running':
      return '生成中'
    case 'ready':
      return '已就绪'
    case 'reviewed':
      return '已检查'
    case 'warning':
      return '需复核'
    case 'error':
      return '异常'
    default:
      return '排队中'
  }
}

export function slideStatusPillClass(status: SlideProgressStatus) {
  switch (status) {
    case 'running':
      return 'is-running'
    case 'ready':
    case 'reviewed':
      return 'is-done'
    case 'warning':
      return 'is-waiting'
    case 'error':
      return 'is-error'
    default:
      return 'is-waiting'
  }
}
