export interface PptGeneratePayload {
  agent_config_id?: number
  description: string
  source_files?: File[]
  mode?: 'quick' | 'step_by_step'
}

export interface PptStreamChunk {
  event_version?: number
  job_id?: string
  conversation_id?: number
  request_id?: string
  event_type?: string
  stage?: string
  message?: string
  channel?: string
  text_delta?: string
  content?: string
  outline_markdown?: string
  payload?: unknown
  artifact_url?: string
  done?: boolean
  error?: string
  error_code?: string
  stage_status?: 'pending' | 'running' | 'waiting' | 'done' | 'warning' | 'error' | string
  progress?: { current: number; total: number }
  slide?: { index: number; version: number }
  severity?: 'info' | 'warning' | 'error' | string
  action_required?: 'approve_outline' | 'choose_style' | null | string
  retryable?: boolean
  sequence?: number
}

export type PptJobStatus =
  | 'queued'
  | 'running'
  | 'waiting_for_outline'
  | 'waiting_for_style'
  | 'exporting'
  | 'success'
  | 'success_with_warnings'
  | 'needs_review'
  | 'failed'
  | 'cancelled'

export interface PptDeckJob {
  job_id: string
  conversation_id: number
  agent_config_id: number
  request_id?: string
  mode: 'quick' | 'step_by_step' | string
  status: PptJobStatus
  stage: string
  error_code?: string
  error_summary?: string
  retryable: boolean
  last_event_sequence: number
  quality: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface PptJobActionPayload {
  action: 'approve_outline' | 'regenerate_outline' | 'choose_style' | 'regenerate_slide' | string
  outline_markdown?: string
  payload?: Record<string, unknown>
}

export interface PptConversationSummary {
  id: number
  agent_config_id: number
  agent_config_name: string
  title: string
  status: 'running' | 'waiting' | 'done' | 'error' | string
  latest_request_id?: string
  created_at: string
  updated_at: string
}

export interface PptConversationDetail extends PptConversationSummary {
  messages: Record<string, unknown>[]
  job?: PptDeckJob
}

export interface ProgressStep {
  id: number
  label: string
  message: string
  stage?: string
  tone: 'normal' | 'error' | 'done'
}

export type SlideProgressStatus = 'pending' | 'running' | 'ready' | 'reviewed' | 'warning' | 'error'

export interface SlideProgress {
  index: number
  title: string
  keyMessage: string
  layout: string
  component: string
  bullets: string[]
  visualSuggestion: string
  speakerNotes: string
  previewPng: string
  previewHtml: string
  previewThumb: string
  version: number
  status: SlideProgressStatus
  warnings: string[]
}

export interface SlideProgressUpdate extends Partial<Omit<SlideProgress, 'index'>> {
  index: number
}

export interface StyleOption {
  styleId: string
  name: string
  coverPng: string
  coverHtml: string
  thumbB64: string
  tokensSummary: string
}

export interface UserMessage {
  id: number
  role: 'user'
  kind: 'user'
  text: string
  fileNames?: string[]
}

export interface StatusMessage {
  id: number
  role: 'assistant'
  kind: 'status'
  title: string
  text: string
  requestId?: string
  state: 'running' | 'waiting' | 'done' | 'error' | 'cancelled'
  steps: ProgressStep[]
  slides: SlideProgress[]
}

export interface OutlineMessage {
  id: number
  role: 'assistant'
  kind: 'outline'
  title: string
  text: string
  requestId: string
  outlineMarkdown: string
  awaitingConfirm: boolean
  showEditor: boolean
}

export interface StyleMessage {
  id: number
  role: 'assistant'
  kind: 'style'
  title: string
  text: string
  requestId: string
  options: StyleOption[]
  awaitingConfirm: boolean
}

export interface ResultMessage {
  id: number
  role: 'assistant'
  kind: 'result'
  title: string
  text: string
  requestId: string
  fileName: string
  warnings: string[]
  degraded: boolean
  qualityStatus: string
}

export type ChatMessage = UserMessage | StatusMessage | OutlineMessage | StyleMessage | ResultMessage

export interface PreviewLightbox {
  src: string
  title: string
  subtitle: string
}

export type PresentationMessageAction =
  | { type: 'preview'; src: string; title: string; subtitle: string }
  | { type: 'approve-outline'; message: OutlineMessage }
  | { type: 'regenerate-outline'; message: OutlineMessage }
  | { type: 'choose-style'; message: StyleMessage; option: StyleOption }
  | { type: 'regenerate-slide'; slide: SlideProgress }
  | { type: 'download'; requestId: string; fileName: string }
  | { type: 'update-outline'; messageId: number; value: string }
  | { type: 'toggle-outline-editor'; messageId: number }
  | { type: 'update-style-feedback'; messageId: number; value: string }
  | { type: 'update-slide-feedback'; slideIndex: number; value: string }

export type PipelineStageStatus = 'done' | 'running' | 'waiting' | 'pending' | 'error'

export interface PipelineStageView {
  key: string
  label: string
  description: string
  status: PipelineStageStatus
}
