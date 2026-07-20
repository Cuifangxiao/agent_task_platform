import type { DownloadResult, HttpClient } from '../../shared/api/httpClient.ts'

import {
  PPT_TASKS_PATH,
  pptTaskArtifactPath,
  pptTaskPath,
  pptTasksListPath,
} from './lib/taskRoutes.ts'
import type { PptCreateTaskPayload, PptTask, PptTaskListOptions } from './types.ts'
import {
  createPresentationWebSocket,
  type PresentationWebSocketClient,
  type PresentationWebSocketHandlers,
} from './websocket.ts'

export interface PresentationApiOptions {
  getAccessToken?: () => string
}

export interface PresentationApi {
  createTask: (payload: PptCreateTaskPayload) => Promise<PptTask>
  listTasks: (options?: PptTaskListOptions) => Promise<PptTask[]>
  getTask: (taskId: string) => Promise<PptTask>
  cancelTask: (taskId: string) => Promise<PptTask>
  downloadTaskFile: (taskId: string, relativePath?: string) => Promise<DownloadResult>
  createRealtimeClient: (handlers: PresentationWebSocketHandlers) => PresentationWebSocketClient
}

export function createPresentationApi(http: HttpClient, options: PresentationApiOptions = {}): PresentationApi {
  const getAccessToken = options.getAccessToken ?? (() => '')
  return {
    createTask: (payload) =>
      http.request<PptTask>(PPT_TASKS_PATH, {
        method: 'POST',
        auth: true,
        body: toTaskFormData(payload),
      }),
    listTasks: (options = {}) =>
      http.request<PptTask[]>(pptTasksListPath(options), { auth: true }),
    getTask: (taskId) =>
      http.request<PptTask>(pptTaskPath(taskId), { auth: true }),
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
    createRealtimeClient: (handlers) => createPresentationWebSocket({ getAccessToken, ...handlers }),
  }
}

export function toTaskFormData(payload: PptCreateTaskPayload) {
  const formData = new FormData()
  formData.append('agent_config_id', String(payload.agent_config_id))
  formData.append('description', payload.description)
  if (payload.thread_id) formData.append('thread_id', payload.thread_id)
  if (payload.client_request_id) formData.append('client_request_id', payload.client_request_id)
  for (const file of payload.source_files ?? []) {
    formData.append('source_files', file)
  }
  return formData
}
