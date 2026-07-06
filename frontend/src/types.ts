export interface SessionState {
  access_token: string
  refresh_token: string
  token_type: string
  session_id: string
  device: string
  access_expires_at: string
  refresh_expires_at: string
}

export interface AuthResponse extends SessionState {
  message: string
  token: string
}

export interface RegisterResponse {
  message: string
}

export interface HealthResponse {
  status: string
}

export interface MeResponse {
  user_id: number
  username: string
  device: string
  session_id: string
}

export interface ProviderParameter {
  name: string
  display_name: string
  type: string
  description: string
  default_value: string
  options: string[]
  required: boolean
  min?: number
  max?: number
}

export interface ProviderModel {
  id: string
  model_name: string
  display_name: string
  type: string
  reasoning_supported: boolean
  parameters: ProviderParameter[]
}

export interface Provider {
  name: string
  display_name: string
  description: string
  manager: string
  sdk: string
  default_base_url: string
  supports_credential_test: boolean
  supports_custom_base_url: boolean
  models: ProviderModel[]
}

export interface ProvidersResponse {
  providers: Provider[]
}

export interface ModelConfig {
  id: number
  name: string
  provider: string
  model_name: string
  base_url: string
  api_key_masked: string
  has_api_key: boolean
  provider_options: Record<string, unknown>
  temperature: number
  max_tokens: number
  is_default: boolean
  status: 'active' | 'inactive'
  created_at: string
  updated_at: string
}

export interface ProviderCredentialTestResponse {
  request_id: string
  provider: string
  status: 'ok' | 'failed' | string
  message: string
}

export interface RequestLogEntry {
  id: number
  time: string
  method: string
  path: string
  status: number | string
  request?: unknown
  response?: unknown
  note?: string
}

export interface ParsedSseEvent {
  event: string
  data: unknown
}

export interface StreamChunk {
  request_id?: string
  delta?: string
  done?: boolean
  error?: string
  sequence?: number
}

export interface PptDeckChunk {
  request_id?: string
  event_type?: string
  stage?: string
  message?: string
  payload?: unknown
  artifact_url?: string
  done?: boolean
  error?: string
  sequence?: number
}
