import type { PptDeckJob, PptJobStatus } from '../types'

export type PptInteractionPhase = 'initial' | 'outline' | 'style' | 'pages' | 'completed' | 'failed'

export function interactionPhase(status: PptJobStatus): PptInteractionPhase {
  switch (status) {
    case 'queued':
      return 'initial'
    case 'waiting_for_outline':
      return 'outline'
    case 'waiting_for_style':
      return 'style'
    case 'running':
    case 'exporting':
      return 'pages'
    case 'success':
    case 'success_with_warnings':
    case 'needs_review':
      return 'completed'
    case 'failed':
    case 'cancelled':
      return 'failed'
  }
}

export function isActivePresentationJob(job: PptDeckJob | undefined | null) {
  return Boolean(job && ['initial', 'pages'].includes(interactionPhase(job.status)))
}

export function eventSequenceDecision(last: number, seen: ReadonlySet<number>, candidate: number) {
  if (!Number.isFinite(candidate) || candidate <= 0) {
    return { accept: true, last }
  }
  return { accept: !seen.has(candidate), last: Math.max(last, candidate) }
}

export function assetCacheKey(requestId: string | undefined, relativePath: string, version = 0) {
  return `${requestId ?? ''}:${relativePath}:v${Math.max(0, version)}`
}

export function modelNameSupportsImageInput(modelName: string) {
  const name = modelName.trim().toLowerCase()
  if (name.includes('qwen') && (name.includes('-vl') || name.includes('vl-'))) {
    return true
  }
  return ['gpt-4o', 'gpt-4.1', 'gpt-5', 'vision', 'gemini', 'claude-3', 'claude-4'].some((marker) =>
    name.includes(marker),
  )
}
