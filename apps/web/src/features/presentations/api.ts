import type { DownloadResult, HttpClient } from '@/shared/api/httpClient'
import { consumeEventStream } from '@/shared/api/sse'
import type { ParsedSseEvent } from '@/shared/api/sse'

import { PPT_TASKS_PATH, pptTaskArtifactPath, pptTaskEventRequest, pptTaskPath } from './lib/taskRoutes'
import type { PptCreateTaskPayload, PptTask } from './types'

export interface PresentationApi {
  createTask: (payload: PptCreateTaskPayload) => Promise<PptTask>
  getTask: (taskId: string) => Promise<PptTask>
  subscribeTask: (
    taskId: string,
    onEvent: (event: ParsedSseEvent) => void,
    signal: AbortSignal,
    after?: number,
  ) => Promise<void>
  cancelTask: (taskId: string) => Promise<PptTask>
  downloadTaskFile: (taskId: string, relativePath?: string) => Promise<DownloadResult>
}

export function createPresentationApi(http: HttpClient): PresentationApi {
  return {
    createTask: (payload) =>
      http.request<PptTask>(PPT_TASKS_PATH, {
        method: 'POST',
        auth: true,
        body: toTaskFormData(payload),
      }),
    getTask: (taskId) =>
      http.request<PptTask>(pptTaskPath(taskId), { auth: true }),
    async subscribeTask(taskId, onEvent, signal, after = 0) {
      const request = pptTaskEventRequest(taskId, after)
      const stream = await http.openStream(request.path, {
        auth: true,
        signal,
        headers: request.headers,
      })
      await consumeEventStream(stream, onEvent)
    },
    cancelTask: (taskId) =>
      http.request<PptTask>(`${pptTaskPath(taskId)}/cancel`, {
        method: 'POST',
        auth: true,
      }),
    downloadTaskFile: (taskId, relativePath) =>
      http.download(
        relativePath ? pptTaskArtifactPath(taskId, relativePath) : `${pptTaskPath(taskId)}/file`,
        { auth: true },
      ),
  }
}

function toTaskFormData(payload: PptCreateTaskPayload) {
  const formData = new FormData()
  formData.append('agent_config_id', String(payload.agent_config_id))
  formData.append('description', payload.description)
  for (const file of payload.source_files ?? []) {
    formData.append('source_files', file)
  }
  return formData
}
