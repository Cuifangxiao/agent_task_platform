export const PPT_TASKS_PATH = '/ppt/tasks'

export function pptTasksListPath(options: { threadId?: string; limit?: number } = {}) {
  const query = new URLSearchParams()
  const threadId = options.threadId?.trim()
  if (threadId) query.set('thread_id', threadId)
  if (Number.isFinite(options.limit) && Number(options.limit) > 0) {
    query.set('limit', String(Math.floor(Number(options.limit))))
  }
  const serialized = query.toString()
  return serialized ? `${PPT_TASKS_PATH}?${serialized}` : PPT_TASKS_PATH
}

export function pptTaskPath(taskId: string) {
  return `${PPT_TASKS_PATH}/${encodeURIComponent(taskId)}`
}

export function pptTaskArtifactPath(taskId: string, relativePath: string) {
  const assetPath = relativePath
    .split(/[\\/]+/)
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join('/')
  return `${pptTaskPath(taskId)}/artifacts/${assetPath}`
}
