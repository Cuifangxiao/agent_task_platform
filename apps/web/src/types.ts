export type Status = 'active' | 'inactive'

export interface SessionState {
  access_token: string
  refresh_token: string
  token_type: string
  session_id: string
  device: string
  access_expires_at: string
  refresh_expires_at: string
}

export interface LoginResponse extends SessionState {
  message: string
  token: string
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
  type: 'string' | 'integer' | 'number' | 'float' | 'boolean' | 'enum' | string
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
  status: Status
  created_at: string
  updated_at: string
}

export interface ModelConfigPayload {
  name: string
  provider: string
  model_name: string
  base_url?: string
  api_key?: string
  provider_options?: Record<string, unknown>
  temperature?: number
  max_tokens?: number
  is_default: boolean
  status: Status
}

export interface AgentNodeDefinition {
  key: string
  display_name: string
  description: string
  requires_model: boolean
}

export interface AgentDefinition {
  id: number
  agent_key: string
  name: string
  description: string
  nodes: AgentNodeDefinition[]
  status: Status
  created_at: string
  updated_at: string
}

export interface AgentConfigNodeBinding {
  node_key: string
  model_config_id: number
  temperature?: number
  max_tokens?: number
}

export interface AgentConfig {
  id: number
  agent_id: number
  agent_key: string
  agent_name: string
  name: string
  description: string
  is_default: boolean
  status: Status
  node_bindings: AgentConfigNodeBinding[]
  created_at: string
  updated_at: string
}

export interface AgentConfigPayload {
  agent_id?: number
  name: string
  description: string
  is_default: boolean
  status: Status
  node_bindings: AgentConfigNodeBinding[]
}

export interface CredentialTestResponse {
  request_id: string
  provider: string
  status: string
  message: string
}


export interface RequestLogEntry {
  id: number
  time: string
  method: string
  path: string
  status: number | string
  note?: string
}
