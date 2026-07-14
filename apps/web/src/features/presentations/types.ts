export interface PptCreateTaskPayload {
  agent_config_id: number
  description: string
  source_files?: File[]
}

export type PptTaskStatus = 'running' | 'succeeded' | 'failed' | 'cancelled'

export type PptEventType =
  | 'TASK_STARTED'
  | 'STEP_STARTED'
  | 'STEP_PROGRESS'
  | 'STEP_COMPLETED'
  | 'ARTIFACT_READY'
  | 'TASK_COMPLETED'
  | 'TASK_FAILED'

export type PptStage =
  | 'PREPARING'
  | 'PLANNING'
  | 'GENERATING_CONTENT'
  | 'GENERATING_ASSETS'
  | 'GENERATING_PAGES'
  | 'RENDERING'
  | 'ASSEMBLING_PPT'
  | 'FINISHED'

export type PptResourceType =
  | 'INPUT_ATTACHMENT'
  | 'IMAGE'
  | 'SVG'
  | 'HTML'
  | 'PAGE_PREVIEW'
  | 'PPTX'
  | 'OTHER_FILE'

export interface PptProgress {
  current: number
  total: number
  percent?: number
  unit?: string
}

export interface PptResource {
  resource_id: string
  type: PptResourceType
  name: string
  url?: string
  relative_path?: string
  mime_type?: string
  size_bytes?: number
}

export interface PptPageDetail {
  page_id?: string
  page_index: number
  page_count?: number
  title?: string
}

export interface PptTaskError {
  code: string
  message: string
  retryable?: boolean
}

export interface PptFinalResult {
  ppt_file?: PptResource
  slide_count?: number
  elapsed_ms?: number
}

export interface PptAgentEvent {
  task_id: string
  sequence: number
  created_at?: string
  type: PptEventType
  stage?: PptStage
  message: string
  progress?: PptProgress
  resources?: PptResource[]
  page?: PptPageDetail
  error?: PptTaskError
  final_result?: PptFinalResult
}

export interface PptTask {
  task_id: string
  status: PptTaskStatus
  stage?: PptStage
  message?: string
  progress?: PptProgress
  result?: PptFinalResult
  error?: PptTaskError
  last_event_sequence?: number
  created_at?: string
  updated_at?: string
  finished_at?: string
  events_url?: string
  file_url?: string
}

export interface PptPagePreview {
  resource: PptResource
  page?: PptPageDetail
  sequence: number
}

export interface PreviewLightbox {
  src: string
  title: string
  subtitle: string
}
