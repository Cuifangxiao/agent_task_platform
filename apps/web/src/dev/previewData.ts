import type { AgentConfig, AgentDefinition, ModelConfig, Provider, RequestLogEntry } from '@/types'
import type { PptAgentEvent, PptTask } from '@/features/presentations/types'

const createdAt = '2026-07-01T08:00:00+08:00'
const previewSlideOne = new URL(
  '../../../runtime/artifacts/decks/03966f90-57f3-40e3-bd07-c75678dc424c/previews/page-001.png',
  import.meta.url,
).href
const previewSlideTwo = new URL(
  '../../../runtime/artifacts/decks/03966f90-57f3-40e3-bd07-c75678dc424c/previews/page-002.png',
  import.meta.url,
).href
const previewSlideThree = new URL(
  '../../../runtime/artifacts/decks/03966f90-57f3-40e3-bd07-c75678dc424c/previews/page-003.png',
  import.meta.url,
).href

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
        id: 'deepseek-v4-flash',
        model_name: 'deepseek-v4-flash',
        display_name: 'DeepSeek V4 Flash',
        type: 'text',
        reasoning_supported: true,
        parameters: [
          {
            name: 'thinking',
            display_name: 'Thinking mode',
            type: 'enum',
            description: '开启后模型会先进行推理，再输出最终回答。',
            default_value: 'enabled',
            options: ['enabled', 'disabled'],
            required: false,
          },
          {
            name: 'reasoning_effort',
            display_name: 'Reasoning effort',
            type: 'enum',
            description: '仅在思考模式开启时生效；High 适合常规任务，Max 适合复杂任务。',
            default_value: 'high',
            options: ['high', 'max'],
            required: false,
          },
          {
            name: 'temperature',
            display_name: 'Temperature',
            type: 'number',
            description: '控制输出随机度；思考模式开启时 DeepSeek 会忽略此参数。',
            default_value: '1',
            options: [],
            required: false,
            min: 0,
            max: 2,
          },
          {
            name: 'max_tokens',
            display_name: 'Max tokens',
            type: 'integer',
            description: '限制单次生成的最大输出长度。',
            default_value: '4096',
            options: [],
            required: false,
            min: 1,
            max: 384000,
          },
        ],
      },
    ],
  },
  {
    name: 'qwen',
    display_name: 'Qwen',
    description: '阿里云通义千问模型服务',
    manager: 'qwen',
    sdk: 'openai-compatible',
    default_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    supports_credential_test: true,
    supports_custom_base_url: true,
    models: [
      {
        id: 'qwen3.6-flash',
        model_name: 'qwen3.6-flash',
        display_name: 'Qwen3.6 Flash',
        type: 'multimodal',
        reasoning_supported: true,
        parameters: [
          {
            name: 'enable_thinking',
            display_name: 'Thinking mode',
            type: 'boolean',
            description: '开启后模型会先推理，再输出最终回答。',
            default_value: 'true',
            options: [],
            required: false,
          },
          {
            name: 'thinking_budget',
            display_name: 'Thinking budget',
            type: 'integer',
            description: '限制思考过程使用的 Token 数。',
            default_value: '',
            options: [],
            required: false,
            min: 1,
          },
          {
            name: 'max_tokens',
            display_name: 'Max tokens',
            type: 'integer',
            description: '限制单次生成的最大输出长度。',
            default_value: '4096',
            options: [],
            required: false,
            min: 1,
            max: 65536,
          },
        ],
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
    model_name: 'deepseek-v4-flash',
    model_type: 'text',
    base_url: 'https://api.deepseek.com',
    api_key_masked: 'sk-••••••••51D0',
    has_api_key: true,
    provider_options: { thinking: 'enabled', reasoning_effort: 'high' },
    temperature: 0.8,
    max_tokens: 8192,
    is_default: false,
    status: 'active',
    created_at: createdAt,
    updated_at: '2026-07-12T16:18:00+08:00',
  },
  {
    id: 3,
    name: 'Qwen 视觉素材',
    provider: 'qwen',
    model_name: 'qwen3.6-flash',
    model_type: 'multimodal',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    api_key_masked: 'sk-••••••••D73C',
    has_api_key: true,
    provider_options: { enable_thinking: true },
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
    description: '从全局大纲、逐页视觉规划到安全 HTML 与本地 PPT 导出的三模型工作流。',
    status: 'active',
    created_at: createdAt,
    updated_at: '2026-07-13T08:00:00+08:00',
    nodes: [
      { key: 'outline', display_name: '全局大纲规划', description: '一次生成整套大纲、页面规划与全局色板。', requires_model: true },
      { key: 'style', display_name: '单页视觉规划', description: '为每页生成继承全局色板的视觉设计方案。', requires_model: true },
      { key: 'designer', display_name: '单页 HTML 生成', description: '按视觉方案生成并修复安全的本地 HTML。', requires_model: true },
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
      { node_key: 'outline', model_config_id: 1 },
      { node_key: 'style', model_config_id: 2 },
      { node_key: 'designer', model_config_id: 3 },
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
      { node_key: 'outline', model_config_id: 2 },
      { node_key: 'style', model_config_id: 2 },
      { node_key: 'designer', model_config_id: 2 },
    ],
    created_at: createdAt,
    updated_at: '2026-07-10T14:20:00+08:00',
  },
]

export const previewLogs: RequestLogEntry[] = [
  { id: 1, time: '08:42:18', method: 'GET', path: '/api/v1/ppt/tasks/preview-101/file', status: 200, note: '下载生成结果' },
  { id: 2, time: '08:35:04', method: 'POST', path: '/api/v1/ppt/tasks', status: 202, note: '创建 PPT 任务' },
  { id: 3, time: '08:23:12', method: 'WS', path: '/api/v1/ppt/ws', status: 101, note: '复用连接订阅生成进度' },
  { id: 4, time: '08:16:47', method: 'PATCH', path: '/api/agent-configs/1', status: 200, note: '更新默认工作流' },
  { id: 5, time: '07:58:31', method: 'POST', path: '/api/providers/credential-tests', status: 200, note: '测试模型凭证' },
  { id: 6, time: '07:46:09', method: 'POST', path: '/api/v1/ppt/tasks', status: 422, note: '资料格式校验失败' },
]

export const previewPptTask: PptTask = {
  schema_version: '1',
  event_id: 'preview-event-14',
  thread_id: 'preview-thread-01',
  run_id: 'preview-run-01',
  task_id: 'preview-run-01',
  description: '生成一份面向投资人的 AI Agent 平台 MVP 汇报，简洁专业，控制在 8 页。',
  agent_config_id: 1,
  status: 'running',
  stage: 'GENERATING_PAGES',
  message: '正在生成第 3/8 页：产品架构',
  last_event_sequence: 14,
  created_at: '2026-07-20T20:58:40+08:00',
  updated_at: '2026-07-20T21:08:42+08:00',
}

export const previewCompletedPptTask: PptTask = {
  schema_version: '1',
  event_id: 'preview-complete-event-10',
  thread_id: 'preview-thread-02',
  run_id: 'preview-run-02',
  task_id: 'preview-run-02',
  description: '制作一份 Transformer 原理培训课件，面向初级工程师，深色科技风格。',
  agent_config_id: 1,
  status: 'succeeded',
  stage: 'FINISHED',
  message: 'PPT 生成完成',
  last_event_sequence: 10,
  result: {
    slide_count: 3,
    ppt_file: { resource_id: 'preview-pptx-02', type: 'PPTX', name: 'transformer-training.pptx' },
  },
  created_at: '2026-07-20T18:21:10+08:00',
  updated_at: '2026-07-20T18:28:42+08:00',
  finished_at: '2026-07-20T18:28:42+08:00',
}

export const previewFailedPptTask: PptTask = {
  schema_version: '1',
  event_id: 'preview-failed-event-05',
  thread_id: 'preview-thread-03',
  run_id: 'preview-run-03',
  task_id: 'preview-run-03',
  description: '根据销售数据生成年度复盘，突出区域增长和重点客户。',
  agent_config_id: 2,
  status: 'failed',
  stage: 'PLANNING',
  message: '读取销售数据时失败',
  error: { code: 'SOURCE_PARSE_FAILED', message: '无法读取附件中的销售数据，请检查文件格式。', retryable: true },
  last_event_sequence: 5,
  created_at: '2026-07-20T16:06:18+08:00',
  updated_at: '2026-07-20T16:07:02+08:00',
  finished_at: '2026-07-20T16:07:02+08:00',
}

export const previewPptTasks: PptTask[] = [
  previewPptTask,
  previewCompletedPptTask,
  previewFailedPptTask,
]

export const previewPptEvents: PptAgentEvent[] = [
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 1, type: 'TASK_STARTED', stage: 'PREPARING', message: 'PPT generation started', created_at: '2026-07-20T20:58:40+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 2, type: 'STEP_COMPLETED', stage: 'PREPARING', message: '输入资料已准备完成', progress: { current: 1, total: 1, percent: 100, unit: 'attachment' }, created_at: '2026-07-20T20:58:42+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 3, type: 'STEP_STARTED', stage: 'PLANNING', message: '正在规划演示文稿结构', created_at: '2026-07-20T20:58:43+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 4, type: 'STEP_COMPLETED', stage: 'PLANNING', message: '全局大纲与样式已完成', created_at: '2026-07-20T21:00:16+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 5, type: 'STEP_STARTED', stage: 'GENERATING_PAGES', message: '正在逐页执行视觉规划与 HTML 生成', created_at: '2026-07-20T21:00:17+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 6, type: 'STEP_PROGRESS', stage: 'GENERATING_PAGES', message: 'style 正在规划第 1 页视觉层级', page: { page_index: 1, page_count: 8, title: '行业瓶颈' }, created_at: '2026-07-20T21:00:32+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 7, type: 'STEP_PROGRESS', stage: 'GENERATING_PAGES', message: 'style 正在规划第 2 页视觉层级', page: { page_index: 2, page_count: 8, title: '核心机制' }, created_at: '2026-07-20T21:00:34+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 8, type: 'STEP_PROGRESS', stage: 'GENERATING_PAGES', message: 'designer 正在生成第 1 页 HTML', page: { page_index: 1, page_count: 8, title: '行业瓶颈' }, created_at: '2026-07-20T21:01:02+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 9, type: 'STEP_PROGRESS', stage: 'GENERATING_PAGES', message: 'designer 正在生成第 2 页 HTML', page: { page_index: 2, page_count: 8, title: '核心机制' }, created_at: '2026-07-20T21:01:05+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 10, type: 'STEP_STARTED', stage: 'GENERATING_PAGES', message: '开始逐页生成，共 8 页', progress: { current: 0, total: 8, percent: 0, unit: 'page' }, created_at: '2026-07-20T21:01:29+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 11, type: 'ARTIFACT_READY', stage: 'RENDERING', message: '第 1/8 页已生成：行业瓶颈', progress: { current: 1, total: 8, percent: 12.5, unit: 'page' }, page: { page_index: 1, page_count: 8, title: '行业瓶颈' }, resources: [{ resource_id: 'preview-page-01', type: 'PAGE_PREVIEW', name: '第 1 页', url: previewSlideOne, mime_type: 'image/png' }], created_at: '2026-07-20T21:04:03+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 12, type: 'ARTIFACT_READY', stage: 'RENDERING', message: '第 2/8 页已生成：核心机制', progress: { current: 2, total: 8, percent: 25, unit: 'page' }, page: { page_index: 2, page_count: 8, title: '核心机制' }, resources: [{ resource_id: 'preview-page-02', type: 'PAGE_PREVIEW', name: '第 2 页', url: previewSlideTwo, mime_type: 'image/png' }], created_at: '2026-07-20T21:07:36+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 13, type: 'STEP_STARTED', stage: 'GENERATING_PAGES', message: '正在设计并生成第 3/8 页', page: { page_index: 3, page_count: 8, title: '产品架构' }, created_at: '2026-07-20T21:07:37+08:00' },
  { task_id: 'preview-run-01', run_id: 'preview-run-01', sequence: 14, type: 'STEP_PROGRESS', stage: 'GENERATING_PAGES', message: '正在生成第 3/8 页：产品架构', progress: { current: 2, total: 8, percent: 25, unit: 'page' }, page: { page_index: 3, page_count: 8, title: '产品架构' }, created_at: '2026-07-20T21:08:42+08:00' },
]

export const previewCompletedPptEvents: PptAgentEvent[] = [
  { task_id: 'preview-run-02', run_id: 'preview-run-02', sequence: 1, type: 'TASK_STARTED', stage: 'PREPARING', message: 'PPT generation started', created_at: '2026-07-20T18:21:10+08:00' },
  { task_id: 'preview-run-02', run_id: 'preview-run-02', sequence: 2, type: 'STEP_COMPLETED', stage: 'PREPARING', message: '输入资料已准备完成', created_at: '2026-07-20T18:21:14+08:00' },
  { task_id: 'preview-run-02', run_id: 'preview-run-02', sequence: 3, type: 'STEP_COMPLETED', stage: 'PLANNING', message: '大纲与叙事结构已完成', created_at: '2026-07-20T18:22:32+08:00' },
  { task_id: 'preview-run-02', run_id: 'preview-run-02', sequence: 4, type: 'STEP_STARTED', stage: 'GENERATING_PAGES', message: '正在逐页执行视觉规划与 HTML 生成', created_at: '2026-07-20T18:23:08+08:00' },
  { task_id: 'preview-run-02', run_id: 'preview-run-02', sequence: 5, type: 'ARTIFACT_READY', stage: 'RENDERING', message: '第 1/3 页已生成', progress: { current: 1, total: 3, percent: 33, unit: 'page' }, page: { page_index: 1, page_count: 3, title: '序列建模的瓶颈' }, resources: [{ resource_id: 'preview-complete-page-01', type: 'PAGE_PREVIEW', name: '第 1 页', url: previewSlideOne, mime_type: 'image/png' }], created_at: '2026-07-20T18:24:12+08:00' },
  { task_id: 'preview-run-02', run_id: 'preview-run-02', sequence: 6, type: 'ARTIFACT_READY', stage: 'RENDERING', message: '第 2/3 页已生成', progress: { current: 2, total: 3, percent: 67, unit: 'page' }, page: { page_index: 2, page_count: 3, title: '自注意力公式' }, resources: [{ resource_id: 'preview-complete-page-02', type: 'PAGE_PREVIEW', name: '第 2 页', url: previewSlideTwo, mime_type: 'image/png' }], created_at: '2026-07-20T18:25:38+08:00' },
  { task_id: 'preview-run-02', run_id: 'preview-run-02', sequence: 7, type: 'ARTIFACT_READY', stage: 'RENDERING', message: '第 3/3 页已生成', progress: { current: 3, total: 3, percent: 100, unit: 'page' }, page: { page_index: 3, page_count: 3, title: '编码器与解码器' }, resources: [{ resource_id: 'preview-complete-page-03', type: 'PAGE_PREVIEW', name: '第 3 页', url: previewSlideThree, mime_type: 'image/png' }], created_at: '2026-07-20T18:27:16+08:00' },
  { task_id: 'preview-run-02', run_id: 'preview-run-02', sequence: 8, type: 'STEP_STARTED', stage: 'ASSEMBLING_PPT', message: '正在组装 PPT 文件', created_at: '2026-07-20T18:27:18+08:00' },
  { task_id: 'preview-run-02', run_id: 'preview-run-02', sequence: 9, type: 'ARTIFACT_READY', stage: 'ASSEMBLING_PPT', message: 'PPT 文件已导出', resources: [{ resource_id: 'preview-pptx-02', type: 'PPTX', name: 'transformer-training.pptx' }], created_at: '2026-07-20T18:28:39+08:00' },
  { task_id: 'preview-run-02', run_id: 'preview-run-02', sequence: 10, type: 'TASK_COMPLETED', stage: 'FINISHED', message: 'PPT 生成完成', final_result: previewCompletedPptTask.result, created_at: '2026-07-20T18:28:42+08:00' },
]

export const previewFailedPptEvents: PptAgentEvent[] = [
  { task_id: 'preview-run-03', run_id: 'preview-run-03', sequence: 1, type: 'TASK_STARTED', stage: 'PREPARING', message: 'PPT generation started', created_at: '2026-07-20T16:06:18+08:00' },
  { task_id: 'preview-run-03', run_id: 'preview-run-03', sequence: 2, type: 'STEP_COMPLETED', stage: 'PREPARING', message: '附件已上传', created_at: '2026-07-20T16:06:24+08:00' },
  { task_id: 'preview-run-03', run_id: 'preview-run-03', sequence: 3, type: 'STEP_STARTED', stage: 'PLANNING', message: '正在读取销售数据', created_at: '2026-07-20T16:06:28+08:00' },
  { task_id: 'preview-run-03', run_id: 'preview-run-03', sequence: 4, type: 'STEP_PROGRESS', stage: 'PLANNING', message: '正在检查表格字段', created_at: '2026-07-20T16:06:40+08:00' },
  { task_id: 'preview-run-03', run_id: 'preview-run-03', sequence: 5, type: 'TASK_FAILED', stage: 'PLANNING', message: '读取销售数据时失败', error: previewFailedPptTask.error, created_at: '2026-07-20T16:07:02+08:00' },
]

export const previewPptEventsByRun: Record<string, PptAgentEvent[]> = {
  'preview-run-01': previewPptEvents,
  'preview-run-02': previewCompletedPptEvents,
  'preview-run-03': previewFailedPptEvents,
}
