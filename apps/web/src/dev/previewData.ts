import type { PptConversationDetail, PptConversationSummary } from '@/features/presentations/types'
import type { AgentConfig, AgentDefinition, ModelConfig, Provider, RequestLogEntry } from '@/types'

const createdAt = '2026-07-01T08:00:00+08:00'

export const previewProviders: Provider[] = [
  {
    name: 'openai',
    display_name: 'OpenAI',
    description: 'OpenAI 官方模型服务',
    manager: 'openai',
    sdk: 'openai',
    default_base_url: 'https://api.openai.com/v1',
    supports_credential_test: true,
    supports_custom_base_url: true,
    models: [
      {
        id: 'gpt-4o',
        model_name: 'gpt-4o',
        display_name: 'GPT-4o',
        type: 'multimodal',
        reasoning_supported: false,
        parameters: [],
      },
    ],
  },
  {
    name: 'deepseek',
    display_name: 'DeepSeek',
    description: 'DeepSeek 模型服务',
    manager: 'deepseek',
    sdk: 'openai-compatible',
    default_base_url: 'https://api.deepseek.com',
    supports_credential_test: true,
    supports_custom_base_url: true,
    models: [
      {
        id: 'deepseek-chat',
        model_name: 'deepseek-chat',
        display_name: 'DeepSeek V3',
        type: 'text',
        reasoning_supported: false,
        parameters: [],
      },
    ],
  },
  {
    name: 'qwen',
    display_name: 'Qwen',
    description: '阿里云通义千问模型服务',
    manager: 'dashscope',
    sdk: 'openai-compatible',
    default_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    supports_credential_test: true,
    supports_custom_base_url: true,
    models: [
      {
        id: 'qwen2.5-vl-72b-instruct',
        model_name: 'qwen2.5-vl-72b-instruct',
        display_name: 'Qwen2.5-VL',
        type: 'multimodal',
        reasoning_supported: false,
        parameters: [],
      },
    ],
  },
]

export const previewModels: ModelConfig[] = [
  {
    id: 1,
    name: 'OpenAI 主模型',
    provider: 'openai',
    model_name: 'gpt-4o',
    model_type: 'multimodal',
    base_url: 'https://api.openai.com/v1',
    api_key_masked: 'sk-••••••••8A2F',
    has_api_key: true,
    provider_options: { supports_image_input: true },
    temperature: 0.7,
    max_tokens: 8192,
    is_default: true,
    status: 'active',
    created_at: createdAt,
    updated_at: '2026-07-13T08:42:00+08:00',
  },
  {
    id: 2,
    name: 'DeepSeek 内容生成',
    provider: 'deepseek',
    model_name: 'deepseek-chat',
    model_type: 'text',
    base_url: 'https://api.deepseek.com',
    api_key_masked: 'sk-••••••••51D0',
    has_api_key: true,
    provider_options: {},
    temperature: 0.8,
    max_tokens: 8192,
    is_default: false,
    status: 'active',
    created_at: createdAt,
    updated_at: '2026-07-12T16:18:00+08:00',
  },
  {
    id: 3,
    name: 'Qwen 视觉审查',
    provider: 'qwen',
    model_name: 'qwen2.5-vl-72b-instruct',
    model_type: 'multimodal',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    api_key_masked: 'sk-••••••••D73C',
    has_api_key: true,
    provider_options: { supports_image_input: true },
    temperature: 0.4,
    max_tokens: 4096,
    is_default: false,
    status: 'active',
    created_at: createdAt,
    updated_at: '2026-07-11T10:06:00+08:00',
  },
]

export const previewAgents: AgentDefinition[] = [
  {
    id: 1,
    agent_key: 'ppt_agent',
    name: 'PPTAgent',
    description: '从需求澄清、大纲规划到视觉审查和 PPT 导出的完整工作流。',
    status: 'active',
    created_at: createdAt,
    updated_at: '2026-07-13T08:00:00+08:00',
    nodes: [
      { key: 'brief', display_name: '需求理解', description: '整理目标、受众和关键信息。', requires_model: true },
      { key: 'outline', display_name: '大纲规划', description: '生成叙事结构与页面节奏。', requires_model: true },
      { key: 'slides', display_name: '页面生成', description: '生成每页内容和视觉结构。', requires_model: true },
      { key: 'review', display_name: '视觉审查', description: '检查版式、可读性和一致性。', requires_model: true },
      { key: 'export', display_name: '导出', description: '生成最终 PPTX 文件。', requires_model: false },
    ],
  },
]

export const previewAgentConfigs: AgentConfig[] = [
  {
    id: 1,
    agent_id: 1,
    agent_key: 'ppt_agent',
    agent_name: 'PPTAgent',
    name: 'PPTAgent 默认工作流',
    description: '适合汇报、方案和研究类演示文稿的通用高质量配置。',
    is_default: true,
    status: 'active',
    node_bindings: [
      { node_key: 'brief', model_config_id: 1 },
      { node_key: 'outline', model_config_id: 1 },
      { node_key: 'slides', model_config_id: 2 },
      { node_key: 'review', model_config_id: 3 },
    ],
    created_at: createdAt,
    updated_at: '2026-07-13T08:36:00+08:00',
  },
  {
    id: 2,
    agent_id: 1,
    agent_key: 'ppt_agent',
    agent_name: 'PPTAgent',
    name: '快速草稿配置',
    description: '用于内部讨论和早期方案验证，优先保证生成速度。',
    is_default: false,
    status: 'active',
    node_bindings: [
      { node_key: 'brief', model_config_id: 2 },
      { node_key: 'outline', model_config_id: 2 },
      { node_key: 'slides', model_config_id: 2 },
      { node_key: 'review', model_config_id: 3 },
    ],
    created_at: createdAt,
    updated_at: '2026-07-10T14:20:00+08:00',
  },
]

export const previewConversations: PptConversationSummary[] = [
  {
    id: 101,
    agent_config_id: 1,
    agent_config_name: 'PPTAgent 默认工作流',
    title: '2026 年半年度产品复盘',
    status: 'done',
    latest_request_id: 'preview-101',
    created_at: '2026-07-13T08:35:00+08:00',
    updated_at: '2026-07-13T08:42:00+08:00',
  },
  {
    id: 102,
    agent_config_id: 1,
    agent_config_name: 'PPTAgent 默认工作流',
    title: 'AI Agent 平台方案',
    status: 'running',
    latest_request_id: 'preview-102',
    created_at: '2026-07-13T08:18:00+08:00',
    updated_at: '2026-07-13T08:23:00+08:00',
  },
  {
    id: 103,
    agent_config_id: 2,
    agent_config_name: '快速草稿配置',
    title: '零售行业趋势分析',
    status: 'waiting',
    latest_request_id: 'preview-103',
    created_at: '2026-07-13T07:46:00+08:00',
    updated_at: '2026-07-13T07:55:00+08:00',
  },
]

export const previewConversationDetails: Record<number, PptConversationDetail> = Object.fromEntries(
  previewConversations.map((conversation) => [conversation.id, { ...conversation, messages: [] }]),
)

export const previewLogs: RequestLogEntry[] = [
  { id: 1, time: '08:42:18', method: 'GET', path: '/api/presentations/artifacts/preview-101', status: 200, note: '下载生成结果' },
  { id: 2, time: '08:35:04', method: 'POST', path: '/api/presentations/jobs', status: 201, note: '创建 PPT 任务' },
  { id: 3, time: '08:23:12', method: 'GET', path: '/api/presentations/jobs/preview-102/events', status: 200, note: '订阅生成进度' },
  { id: 4, time: '08:16:47', method: 'PATCH', path: '/api/agent-configs/1', status: 200, note: '更新默认工作流' },
  { id: 5, time: '07:58:31', method: 'POST', path: '/api/providers/credential-tests', status: 200, note: '测试模型凭证' },
  { id: 6, time: '07:46:09', method: 'POST', path: '/api/presentations/jobs', status: 422, note: '资料格式校验失败' },
]
