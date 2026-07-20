import { createPresentationApi } from '@/features/presentations/api'
import { createHttpClient } from '@/shared/api/httpClient'
import type { HttpClientOptions } from '@/shared/api/httpClient'
import type {
  AgentConfig,
  AgentConfigPayload,
  AgentDefinition,
  CredentialTestResponse,
  LoginResponse,
  MeResponse,
  ModelConfig,
  ModelConfigPayload,
  Provider,
} from '@/types'

export type ApiClientOptions = HttpClientOptions
export { ApiError, toSession } from '@/shared/api/httpClient'

/**
 * Application API facade. Transport concerns live in shared/api while each
 * feature owns its endpoint map (for example `presentations`).
 */
export function createApiClient(options: ApiClientOptions) {
  const http = createHttpClient(options)

  return {
    login: (username: string, password: string) =>
      http.request<LoginResponse>('/auth/login', {
        method: 'POST',
        body: { username, password, device: 'web' },
      }),
    register: (username: string, password: string) =>
      http.request<{ message: string }>('/auth/register', { method: 'POST', body: { username, password } }),
    logout: () => http.request<{ message: string }>('/auth/logout', { method: 'POST', auth: true }),
    me: () => http.request<MeResponse>('/auth/me', { auth: true }),
    health: () => http.request<{ status: string }>('/health'),

    providers: () => http.request<{ providers: Provider[] }>('/providers', { auth: true }),
    testCredential: (payload: { provider: string; api_key: string; base_url?: string }) =>
      http.request<CredentialTestResponse>('/providers/credential-tests', {
        method: 'POST',
        auth: true,
        body: payload,
      }),

    modelConfigs: () => http.request<ModelConfig[]>('/model-configs', { auth: true }),
    createModelConfig: (payload: ModelConfigPayload) =>
      http.request<ModelConfig>('/model-configs', { method: 'POST', auth: true, body: payload }),
    updateModelConfig: (id: number, payload: Partial<ModelConfigPayload>) =>
      http.request<ModelConfig>(`/model-configs/${id}`, { method: 'PATCH', auth: true, body: payload }),
    deleteModelConfig: (id: number) =>
      http.request<{ message: string }>(`/model-configs/${id}`, { method: 'DELETE', auth: true }),

    agents: () => http.request<AgentDefinition[]>('/agents', { auth: true }),
    agentConfigs: () => http.request<AgentConfig[]>('/agent-configs', { auth: true }),
    createAgentConfig: (payload: AgentConfigPayload & { agent_id: number }) =>
      http.request<AgentConfig>('/agent-configs', { method: 'POST', auth: true, body: payload }),
    updateAgentConfig: (id: number, payload: AgentConfigPayload) =>
      http.request<AgentConfig>(`/agent-configs/${id}`, { method: 'PATCH', auth: true, body: payload }),
    deleteAgentConfig: (id: number) =>
      http.request<{ message: string }>(`/agent-configs/${id}`, { method: 'DELETE', auth: true }),

    presentations: createPresentationApi(http, {
      getAccessToken: () => options.getSession()?.access_token ?? '',
    }),
  }
}
