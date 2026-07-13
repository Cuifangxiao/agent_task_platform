<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Database, Image as ImageIcon, KeyRound, MessageSquareText, Plus, Save, ScanEye, Search, Trash2, X } from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
import ModelTypeBadge from '@/components/ModelTypeBadge.vue'
import ProviderIcon from '@/components/ProviderIcon.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { MODEL_TYPE_OPTIONS, inferModelType, normalizeModelType } from '@/features/models/modelPresentation'
import type { ModelConfig, ModelConfigPayload, ModelType, Provider, ProviderParameter, Status } from '@/types'

const props = defineProps<{
  providers: Provider[]
  models: ModelConfig[]
  saving: boolean
}>()

const emit = defineEmits<{
  save: [payload: ModelConfigPayload, id: number | undefined, callbacks: FormActionCallbacks]
  delete: [id: number, callbacks: FormActionCallbacks]
  testCredential: [provider: string, apiKey: string, baseUrl: string | undefined, callbacks: FormActionCallbacks]
}>()

interface FormActionCallbacks {
  onError: (message: string) => void
  onSuccess?: (message?: string) => void
}

interface Draft {
  id?: number
  name: string
  provider: string
  model_name: string
  model_type: ModelType
  base_url: string
  api_key: string
  provider_options: Record<string, unknown>
  temperature: number
  max_tokens: number
  is_default: boolean
  status: Status
}

const draft = reactive<Draft>(emptyDraft())
const formError = ref('')
const formNotice = ref('')
const isEditorOpen = ref(false)
const selectedTypeFilter = ref<'all' | ModelType>('all')
const searchQuery = ref('')

const selectedProvider = computed(() => props.providers.find((item) => item.name === draft.provider) ?? null)
const selectedModel = computed(
  () => selectedProvider.value?.models.find((item) => item.model_name === draft.model_name) ?? null,
)
const dynamicParameters = computed(() =>
  (selectedModel.value?.parameters ?? []).filter((item) => !['temperature', 'max_tokens'].includes(item.name)),
)
const activeModels = computed(() => props.models.filter((item) => item.status === 'active'))
const defaultModel = computed(() => props.models.find((item) => item.is_default) ?? null)
const visibleModels = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return props.models.filter((model) => {
    const matchesType = selectedTypeFilter.value === 'all' || normalizeModelType(model.model_type) === selectedTypeFilter.value
    const matchesQuery = !query || [model.name, model.provider, model.model_name].some((value) => value.toLowerCase().includes(query))
    return matchesType && matchesQuery
  })
})
const typeFilters = computed(() => [
  { value: 'all' as const, label: '全部', count: props.models.length },
  ...MODEL_TYPE_OPTIONS.map((option) => ({
    value: option.value,
    label: option.label,
    count: props.models.filter((model) => normalizeModelType(model.model_type) === option.value).length,
  })),
])

watch(
  () => props.providers,
  () => {
    if (!draft.provider && props.providers.length > 0) {
      draft.provider = props.providers[0].name
      syncProviderDefaults()
    }
  },
  { immediate: true },
)

watch(
  () => draft.provider,
  () => syncProviderDefaults(),
  { flush: 'sync' },
)

watch(
  () => draft.model_name,
  () => {
    syncParameterDefaults()
    draft.model_type = inferModelType(selectedModel.value)
  },
  { flush: 'sync' },
)

function emptyDraft(): Draft {
  return {
    name: '',
    provider: '',
    model_name: '',
    model_type: 'text',
    base_url: '',
    api_key: '',
    provider_options: {},
    temperature: 1,
    max_tokens: 4096,
    is_default: false,
    status: 'active',
  }
}

function syncProviderDefaults() {
  const provider = selectedProvider.value
  if (!provider) {
    return
  }
  draft.base_url = provider.default_base_url
  draft.model_name = provider.models[0]?.model_name ?? ''
  draft.model_type = inferModelType(provider.models[0])
  syncParameterDefaults()
}

function syncParameterDefaults() {
  const next: Record<string, unknown> = {}
  for (const parameter of dynamicParameters.value) {
    next[parameter.name] = draft.provider_options[parameter.name] ?? normalizeParameterValue(parameter, parameter.default_value)
  }
  draft.provider_options = next
}

function normalizeParameterValue(parameter: ProviderParameter, value: unknown) {
  if (parameter.type === 'integer') {
    return Number.parseInt(String(value || '0'), 10)
  }
  if (parameter.type === 'number' || parameter.type === 'float') {
    return Number.parseFloat(String(value || '0'))
  }
  if (parameter.type === 'boolean') {
    return value === true || value === 'true'
  }
  return value ?? ''
}

function edit(model: ModelConfig) {
  clearFormFeedback()
  Object.assign(draft, {
    id: model.id,
    name: model.name,
    provider: model.provider,
    model_name: model.model_name,
    model_type: normalizeModelType(model.model_type),
    base_url: model.base_url,
    api_key: '',
    provider_options: { ...model.provider_options },
    temperature: model.temperature,
    max_tokens: model.max_tokens,
    is_default: model.is_default,
    status: model.status,
  })
  // The provider catalog is the capability source of truth. Reconcile stale
  // records before the next save rather than exposing an invalid combination.
  draft.model_type = selectedModel.value ? inferModelType(selectedModel.value) : normalizeModelType(model.model_type)
  isEditorOpen.value = true
}

function reset() {
  clearFormFeedback()
  Object.assign(draft, emptyDraft())
  if (props.providers[0]) {
    draft.provider = props.providers[0].name
  }
  isEditorOpen.value = true
}

function closeEditor() {
  isEditorOpen.value = false
  clearFormFeedback()
}

function submit() {
  clearFormFeedback()
  const payload: ModelConfigPayload = {
    name: draft.name.trim(),
    provider: draft.provider,
    model_name: draft.model_name,
    model_type: draft.model_type,
    base_url: draft.base_url.trim() || undefined,
    provider_options: draft.provider_options,
    temperature: Number(draft.temperature),
    max_tokens: Number(draft.max_tokens),
    is_default: draft.is_default,
    status: draft.status,
  }

  if (draft.api_key.trim()) {
    payload.api_key = draft.api_key.trim()
  }

  emit('save', payload, draft.id, {
    onError: showFormError,
    onSuccess: closeEditor,
  })
}

function testCredential() {
  clearFormFeedback()
  emit('testCredential', draft.provider, draft.api_key, draft.base_url || undefined, {
    onError: showFormError,
    onSuccess: (message) => {
      formNotice.value = message || '凭证测试完成。'
    },
  })
}

function showFormError(message: string) {
  formNotice.value = ''
  formError.value = message
}

function clearFormFeedback() {
  formError.value = ''
  formNotice.value = ''
}

function modelTypeIcon(modelType: ModelType) {
  if (modelType === 'multimodal') return ScanEye
  if (modelType === 'image') return ImageIcon
  return MessageSquareText
}
</script>

<template>
  <section class="page management-page model-config-page">
    <header class="page-head management-head">
      <div class="page-title-block">
        <span class="page-eyebrow">模型与凭证</span>
        <h2>管理模型与凭证</h2>
        <p>集中管理 Provider、模型能力与默认调用参数，为 Agent 节点提供稳定的模型来源。</p>
      </div>
      <button class="btn primary" type="button" @click="reset">
        <Plus :size="18" />
        新建模型配置
      </button>
    </header>

    <div class="page-summary" aria-label="模型配置概览">
      <div><span>配置总数</span><strong>{{ models.length }}</strong></div>
      <div><span>已启用</span><strong>{{ activeModels.length }}</strong></div>
      <div class="summary-wide"><span>默认模型</span><strong>{{ defaultModel?.name || '尚未设置' }}</strong></div>
    </div>

    <EmptyState
      v-if="models.length === 0"
      :icon="Database"
      title="还没有模型配置"
      text="先创建一个 active Model Config，之后才能给 Agent 节点绑定模型。"
      action="新建模型配置"
      @action="reset"
    />

    <section v-else class="content-panel catalog-panel" aria-labelledby="model-list-title">
      <div class="content-panel-head catalog-toolbar">
        <div>
          <h3 id="model-list-title">全部模型</h3>
          <p>选择一项查看凭证状态、能力类型和调用参数。</p>
        </div>
        <label class="search-field">
          <Search :size="17" />
          <input v-model="searchQuery" type="search" placeholder="搜索名称、Provider 或模型" />
        </label>
      </div>

      <div class="catalog-filter-row">
        <div class="model-type-filters" role="group" aria-label="按模型类型筛选">
          <button
            v-for="filter in typeFilters"
            :key="filter.value"
            class="model-type-filter"
            :class="{ active: selectedTypeFilter === filter.value }"
            type="button"
            :aria-pressed="selectedTypeFilter === filter.value"
            @click="selectedTypeFilter = filter.value"
          >
            {{ filter.label }}
            <span>{{ filter.count }}</span>
          </button>
        </div>
        <span class="result-count">显示 {{ visibleModels.length }} 项</span>
      </div>

      <div v-if="visibleModels.length" class="config-list">
        <button v-for="model in visibleModels" :key="model.id" class="config-row model-config-row" type="button" @click="edit(model)">
          <span class="config-row-icon"><ProviderIcon :provider="model.provider" :size="42" /></span>
          <span class="config-row-main">
            <span class="config-row-title">
              <strong>{{ model.name }}</strong>
              <StatusBadge v-if="model.is_default" tone="blue" label="默认" />
            </span>
            <span>{{ model.provider }} · {{ model.model_name }}</span>
          </span>
          <span class="config-row-detail">
            <small>调用参数</small>
            <strong>T {{ model.temperature }} · {{ model.max_tokens.toLocaleString() }} tokens</strong>
          </span>
          <span class="config-row-badges">
            <ModelTypeBadge :model-type="model.model_type" />
            <StatusBadge :tone="model.status === 'active' ? 'green' : 'gray'" :label="model.status === 'active' ? '已启用' : '已停用'" />
          </span>
        </button>
      </div>

      <div v-else class="model-filter-empty">没有匹配当前筛选条件的模型配置。</div>
    </section>

    <Teleport to="body">
      <div v-if="isEditorOpen" class="modal-backdrop" @click.self="closeEditor">
        <form class="modal-panel config-modal" role="dialog" aria-modal="true" @submit.prevent="submit">
          <div class="modal-head">
            <div>
              <h2>{{ draft.id ? '编辑模型配置' : '新建模型配置' }}</h2>
              <p>{{ draft.id ? '留空 API Key 会保留原密钥。' : '填写模型凭证和默认调用参数。' }}</p>
              <p v-if="formError" class="form-error modal-feedback" role="alert">{{ formError }}</p>
              <p v-else-if="formNotice" class="form-notice modal-feedback" role="status">{{ formNotice }}</p>
            </div>
            <div class="modal-head-actions">
              <button
                v-if="draft.id"
                class="icon-btn danger"
                type="button"
                title="删除"
                @click="emit('delete', draft.id, { onError: showFormError, onSuccess: closeEditor })"
              >
                <Trash2 :size="18" />
              </button>
              <button class="icon-btn" type="button" title="关闭" @click="closeEditor">
                <X :size="18" />
              </button>
            </div>
          </div>

          <div class="modal-body">
            <label>
              配置名称
              <input v-model="draft.name" required maxlength="64" placeholder="例如：高级模型" />
            </label>

            <div class="form-grid">
              <label>
                Provider
                <div class="select-with-provider-icon">
                  <ProviderIcon v-if="draft.provider" :provider="draft.provider" :size="36" />
                  <select v-model="draft.provider" required>
                    <option v-for="provider in providers" :key="provider.name" :value="provider.name">
                      {{ provider.display_name || provider.name }}
                    </option>
                  </select>
                </div>
              </label>
              <label>
                Model
                <select v-model="draft.model_name" required>
                  <option v-for="model in selectedProvider?.models ?? []" :key="model.id" :value="model.model_name">
                    {{ model.display_name || model.model_name }}
                  </option>
                </select>
              </label>
            </div>

            <fieldset class="model-type-fieldset" aria-describedby="model-type-help">
              <legend>模型类型</legend>
              <div class="model-type-options">
                <label
                  v-for="option in MODEL_TYPE_OPTIONS"
                  :key="option.value"
                  class="model-type-option"
                  :class="{ active: draft.model_type === option.value }"
                >
                  <input v-model="draft.model_type" type="radio" name="model-type" :value="option.value" disabled />
                  <component :is="modelTypeIcon(option.value)" :size="20" aria-hidden="true" />
                  <span>
                    <strong>{{ option.label }}</strong>
                    <small>{{ option.description }}</small>
                  </span>
                </label>
              </div>
              <p id="model-type-help" class="model-type-help">类型由 Provider 模型目录自动同步，切换 Model 后会自动更新。</p>
            </fieldset>

            <label>
              Base URL
              <input v-model="draft.base_url" placeholder="https://api.example.com" />
            </label>

            <label>
              API Key
              <input v-model="draft.api_key" type="password" :required="!draft.id" placeholder="sk-..." />
            </label>

            <div class="form-grid">
              <label>
                Temperature
                <input v-model.number="draft.temperature" type="number" min="0" max="2" step="0.1" />
              </label>
              <label>
                Max tokens
                <input v-model.number="draft.max_tokens" type="number" min="1" step="1" />
              </label>
            </div>

            <div v-if="dynamicParameters.length" class="subsection">
              <h3>Provider Options</h3>
              <label v-for="parameter in dynamicParameters" :key="parameter.name">
                {{ parameter.display_name || parameter.name }}
                <select v-if="parameter.type === 'enum'" v-model="draft.provider_options[parameter.name]">
                  <option v-for="option in parameter.options" :key="option" :value="option">{{ option }}</option>
                </select>
                <select v-else-if="parameter.type === 'boolean'" v-model="draft.provider_options[parameter.name]">
                  <option :value="true">true</option>
                  <option :value="false">false</option>
                </select>
                <input
                  v-else
                  v-model="draft.provider_options[parameter.name]"
                  :type="parameter.type === 'integer' || parameter.type === 'number' ? 'number' : 'text'"
                  :min="parameter.min"
                  :max="parameter.max"
                />
              </label>
            </div>

            <div class="check-row">
              <label><input v-model="draft.is_default" type="checkbox" /> 设为默认</label>
              <label>
                状态
                <select v-model="draft.status">
                  <option value="active">active</option>
                  <option value="inactive">inactive</option>
                </select>
              </label>
            </div>
          </div>

          <div class="modal-actions">
            <button class="btn ghost" type="button" :disabled="!draft.provider || !draft.api_key" @click="testCredential">
              <KeyRound :size="18" />
              测试凭证
            </button>
            <button class="btn primary" type="submit" :disabled="saving">
              <Save :size="18" />
              保存模型配置
            </button>
          </div>
        </form>
      </div>
    </Teleport>
  </section>
</template>
