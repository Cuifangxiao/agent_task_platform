import type { ModelType, ProviderModel } from '@/types'

export const MODEL_TYPE_OPTIONS: ReadonlyArray<{
  value: ModelType
  label: string
  description: string
}> = [
  { value: 'text', label: '文本模型', description: '文本理解、推理与生成' },
  { value: 'multimodal', label: '多模态模型', description: '可理解图片等多种输入' },
  { value: 'image', label: '生图模型', description: '根据提示词生成或编辑图片' },
]

export type ProviderBrand = 'openai' | 'qwen' | 'deepseek' | 'gemini' | 'kimi' | 'generic'

export function normalizeModelType(value: unknown): ModelType {
  return value === 'multimodal' || value === 'image' ? value : 'text'
}

export function modelTypeLabel(value: unknown) {
  const normalized = normalizeModelType(value)
  return MODEL_TYPE_OPTIONS.find((option) => option.value === normalized)?.label ?? '文本模型'
}

export function inferModelType(model?: Pick<ProviderModel, 'model_name' | 'type'> | null): ModelType {
  if (!model) {
    return 'text'
  }

  const catalogType = model.type.trim().toLowerCase()
  if (catalogType === 'text' || catalogType === 'multimodal' || catalogType === 'image') {
    return catalogType
  }
  if (catalogType === 'chat') {
    // Older provider catalogs label every chat-compatible model as `chat`,
    // including vision models. Keep the name fallback for those catalogs.
    return inferModelTypeFromName(model.model_name)
  }
  return inferModelTypeFromName(model.model_name)
}

export function inferModelTypeFromName(modelName: string): ModelType {
  const name = modelName.trim().toLowerCase()
  if (/dall[-_. ]?e|gpt[-_. ]?image|image[-_. ]?(?:gen|generation)|stable[-_. ]?diffusion|flux(?:[-_. ]|$)|midjourney|text[-_. ]?to[-_. ]?image/.test(name)) {
    return 'image'
  }
  if (/(?:^|[-_. ])vl(?:[-_. ]|$)|vision|multimodal|gpt-4o|gpt-4\.1|gpt-5|gemini|claude-[34]/.test(name)) {
    return 'multimodal'
  }
  return 'text'
}

export function resolveProviderBrand(provider: string): ProviderBrand {
  const value = provider.trim().toLowerCase()
  if (value.includes('openai') || value === 'azure') return 'openai'
  if (value.includes('qwen') || value.includes('dashscope') || value.includes('tongyi')) return 'qwen'
  if (value.includes('deepseek')) return 'deepseek'
  if (value.includes('gemini')) return 'gemini'
  if (value.includes('kimi') || value.includes('moonshot')) return 'kimi'
  return 'generic'
}

export function providerBrandLabel(provider: string) {
  const brand = resolveProviderBrand(provider)
  if (brand === 'openai') return 'OpenAI'
  if (brand === 'qwen') return 'Qwen'
  if (brand === 'deepseek') return 'DeepSeek'
  if (brand === 'gemini') return 'Google Gemini'
  if (brand === 'kimi') return 'Kimi'
  return provider || '自定义厂商'
}
