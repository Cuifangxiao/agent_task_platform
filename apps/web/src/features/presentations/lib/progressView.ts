import type { PipelineStageStatus, PipelineStageView, SlideProgressStatus, StatusMessage } from '../types'
export const pipelineStages = [
  { key: 'brief', label: '理解需求', description: '整理目标、受众和上下文' },
  { key: 'outline', label: '规划大纲', description: '生成 PPT 结构和每页重点' },
  { key: 'outline_review', label: '确认大纲', description: '等待你确认或调整大纲' },
  { key: 'style', label: '准备风格', description: '生成视觉方向和封面样张' },
  { key: 'style_review', label: '选择风格', description: '等待选择视觉方向' },
  { key: 'writer', label: '撰写页面', description: '把大纲扩展成逐页结构化内容' },
  { key: 'designer', label: '设计页面', description: '选择版式并渲染 HTML 和 PNG 预览' },
  { key: 'validate', label: '质量校验', description: '检查版式、密度和结构' },
  { key: 'repair', label: '自动修复', description: '修复校验发现的问题' },
  { key: 'review_vlm', label: '视觉复核', description: '检查视觉质量和完整性' },
  { key: 'coherence', label: '统一表达', description: '检查跨页连贯性' },
  { key: 'export', label: '导出 PPT', description: '生成可下载的 PPT 文件' },
  { key: 'artifact', label: '完成结果', description: '准备下载文件' },
] as const

export function normalizeProgressStage(value: string | undefined) {
  switch (value) {
    case 'brief':
    case 'outline':
    case 'outline_review':
    case 'style':
    case 'style_review':
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
  return [...message.steps].reverse().find((step) => step.stage && step.stage !== 'error') ?? null
}

export function currentProgressLabel(message: StatusMessage) {
  const latest = latestProgressStep(message)
  if (!latest) {
    return message.text
  }
  return latest.message || message.text
}

export function currentProgressStageLabel(message: StatusMessage) {
  const latest = latestProgressStep(message)
  if (!latest?.stage) {
    return message.state === 'waiting' ? '等待下一步' : '准备开始'
  }
  return pipelineStages.find((stage) => stage.key === latest.stage)?.label ?? latest.label
}

export function progressChecklist(message: StatusMessage): PipelineStageView[] {
  const latest = latestProgressStep(message)
  const latestKey = latest?.stage ?? ''
  const latestIndex = pipelineStages.findIndex((stage) => stage.key === latestKey)
  const failed = message.state === 'error'

  return pipelineStages.map((stage, index) => {
    let status: PipelineStageStatus = 'pending'
    if (message.state === 'done') {
      status = 'done'
    } else if (latestIndex >= 0 && index < latestIndex) {
      status = 'done'
    } else if (latestIndex >= 0 && index === latestIndex) {
      status = failed ? 'error' : message.state === 'waiting' ? 'waiting' : 'running'
    }
    return {
      ...stage,
      status,
    }
  })
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
      return 'Generating'
    case 'ready':
      return 'Ready'
    case 'reviewed':
      return 'Checked'
    case 'warning':
      return 'Review'
    case 'error':
      return 'Error'
    default:
      return 'Queued'
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

