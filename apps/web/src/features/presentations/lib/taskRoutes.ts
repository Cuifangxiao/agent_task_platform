export const PPT_TASKS_PATH = '/ppt/tasks'

export function pptTaskPath(taskId: string) {
  return `${PPT_TASKS_PATH}/${encodeURIComponent(taskId)}`
}

export function pptTaskEventRequest(taskId: string, after = 0) {
  const cursor = Number.isFinite(after) && after > 0 ? Math.floor(after) : 0
  return {
    path: `${pptTaskPath(taskId)}/events${cursor ? `?after=${cursor}` : ''}`,
    headers: cursor ? { 'Last-Event-ID': String(cursor) } : undefined,
  }
}

export function pptTaskArtifactPath(taskId: string, relativePath: string) {
  const assetPath = relativePath
    .split(/[\\/]+/)
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join('/')
  return `${pptTaskPath(taskId)}/artifacts/${assetPath}`
}
