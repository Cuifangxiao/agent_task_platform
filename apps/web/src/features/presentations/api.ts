import type { DownloadResult, HttpClient } from '@/shared/api/httpClient'
import { consumeEventStream } from '@/shared/api/sse'
import type { ParsedSseEvent } from '@/shared/api/sse'

import type {
  PptConversationDetail,
  PptConversationSummary,
  PptDeckJob,
  PptGeneratePayload,
  PptJobActionPayload,
} from './types'

const PRESENTATIONS_PATH = '/presentations'

export interface PresentationApi {
  listConversations: () => Promise<PptConversationSummary[]>
  getConversation: (id: number) => Promise<PptConversationDetail>
  deleteConversation: (id: number) => Promise<{ deleted: boolean; id: number }>
  createJob: (payload: PptGeneratePayload) => Promise<PptDeckJob>
  getJob: (jobId: string) => Promise<PptDeckJob>
  subscribeToJob: (
    jobId: string,
    onEvent: (event: ParsedSseEvent) => void,
    signal: AbortSignal,
    lastEventId?: number,
  ) => Promise<void>
  submitJobAction: (
    jobId: string,
    payload: PptJobActionPayload,
  ) => Promise<{ job: PptDeckJob; duplicate: boolean }>
  cancelJob: (jobId: string) => Promise<PptDeckJob>
  downloadArtifact: (requestId: string) => Promise<DownloadResult>
  downloadAsset: (requestId: string, relativePath: string) => Promise<Blob>
}

export function createPresentationApi(http: HttpClient): PresentationApi {
  return {
    listConversations: () =>
      http.request<PptConversationSummary[]>(`${PRESENTATIONS_PATH}/conversations`, { auth: true }),
    getConversation: (id) =>
      http.request<PptConversationDetail>(`${PRESENTATIONS_PATH}/conversations/${id}`, { auth: true }),
    deleteConversation: (id) =>
      http.request<{ deleted: boolean; id: number }>(`${PRESENTATIONS_PATH}/conversations/${id}`, {
        method: 'DELETE',
        auth: true,
      }),
    createJob: (payload) =>
      http.request<PptDeckJob>(`${PRESENTATIONS_PATH}/jobs`, {
        method: 'POST',
        auth: true,
        body: toGenerateFormData(payload),
      }),
    getJob: (jobId) =>
      http.request<PptDeckJob>(`${PRESENTATIONS_PATH}/jobs/${encodeURIComponent(jobId)}`, { auth: true }),
    async subscribeToJob(jobId, onEvent, signal, lastEventId = 0) {
      const stream = await http.openStream(`${PRESENTATIONS_PATH}/jobs/${encodeURIComponent(jobId)}/events`, {
        auth: true,
        signal,
        headers: lastEventId > 0 ? { 'Last-Event-ID': String(lastEventId) } : undefined,
      })
      await consumeEventStream(stream, onEvent)
    },
    submitJobAction: (jobId, payload) =>
      http.request<{ job: PptDeckJob; duplicate: boolean }>(
        `${PRESENTATIONS_PATH}/jobs/${encodeURIComponent(jobId)}/actions`,
        { method: 'POST', auth: true, body: payload },
      ),
    cancelJob: (jobId) =>
      http.request<PptDeckJob>(`${PRESENTATIONS_PATH}/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: 'POST',
        auth: true,
      }),
    downloadArtifact: (requestId) =>
      http.download(`${PRESENTATIONS_PATH}/artifacts/${encodeURIComponent(requestId)}`, { auth: true }),
    downloadAsset: (requestId, relativePath) =>
      http.downloadBlob(artifactPath(requestId, relativePath), { auth: true }),
  }
}

function toGenerateFormData(payload: PptGeneratePayload) {
  const formData = new FormData()
  if (payload.agent_config_id !== undefined) {
    formData.append('agent_config_id', String(payload.agent_config_id))
  }
  formData.append('description', payload.description)
  formData.append('mode', payload.mode ?? 'step_by_step')
  for (const file of payload.source_files ?? []) {
    formData.append('source_files', file)
  }
  return formData
}

function artifactPath(requestId: string, relativePath: string) {
  const assetPath = relativePath
    .split('/')
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join('/')
  return `${PRESENTATIONS_PATH}/artifacts/${encodeURIComponent(requestId)}/${assetPath}`
}
