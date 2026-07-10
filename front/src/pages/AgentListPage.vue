<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Bot, Plus, Save, Trash2, X } from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
import NodeBindingList from '@/components/NodeBindingList.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { modelNameSupportsImageInput } from '@/pptAgentWorkflow'
import type { AgentConfig, AgentConfigNodeBinding, AgentConfigPayload, AgentDefinition, ModelConfig, Status } from '@/types'

const props = defineProps<{
  agents: AgentDefinition[]
  models: ModelConfig[]
  agentConfigs: AgentConfig[]
  saving: boolean
}>()

const emit = defineEmits<{
  save: [payload: AgentConfigPayload & { agent_id?: number }, id?: number]
  delete: [id: number]
  goPpt: []
}>()

interface BindingDraft {
  node_key: string
  model_config_id: string
  temperature: string
  max_tokens: string
}

interface Draft {
  id?: number
  agent_id: string
  name: string
  description: string
  is_default: boolean
  status: Status
  node_bindings: BindingDraft[]
}

const draft = reactive<Draft>(emptyDraft())
const validationError = ref('')
const isEditorOpen = ref(false)
const activeModels = computed(() => props.models.filter((item) => item.status === 'active'))
const selectedAgent = computed(() => props.agents.find((item) => item.id === Number(draft.agent_id)) ?? null)
const requiredNodes = computed(() => selectedAgent.value?.nodes.filter((node) => node.requires_model) ?? [])

watch(
  () => props.agents,
  () => {
    if (!draft.agent_id && props.agents.length > 0) {
      draft.agent_id = String(props.agents[0].id)
      syncNodeBindings()
    }
  },
  { immediate: true },
)

watch(
  () => draft.agent_id,
  () => syncNodeBindings(),
)

function emptyDraft(): Draft {
  return {
    agent_id: '',
    name: '',
    description: '',
    is_default: false,
    status: 'active',
    node_bindings: [],
  }
}

function syncNodeBindings() {
  const previous = new Map(draft.node_bindings.map((item) => [item.node_key, item]))
  draft.node_bindings = requiredNodes.value.map((node) => previous.get(node.key) ?? {
    node_key: node.key,
    model_config_id: '',
    temperature: '',
    max_tokens: '',
  })
}

function edit(config: AgentConfig) {
  validationError.value = ''
  Object.assign(draft, {
    id: config.id,
    agent_id: String(config.agent_id),
    name: config.name,
    description: config.description,
    is_default: config.is_default,
    status: config.status,
    node_bindings: config.node_bindings.map((binding) => ({
      node_key: binding.node_key,
      model_config_id: String(binding.model_config_id),
      temperature: binding.temperature === undefined ? '' : String(binding.temperature),
      max_tokens: binding.max_tokens === undefined ? '' : String(binding.max_tokens),
    })),
  })
  syncNodeBindings()
  isEditorOpen.value = true
}

function reset(agentId?: number) {
  validationError.value = ''
  Object.assign(draft, emptyDraft())
  draft.agent_id = String(agentId ?? props.agents[0]?.id ?? '')
  syncNodeBindings()
  isEditorOpen.value = true
}

function closeEditor() {
  isEditorOpen.value = false
}

function nodeMeta(nodeKey: string) {
  return requiredNodes.value.find((node) => node.key === nodeKey)
}

function bindingVisionWarning(nodeKey: string, modelConfigId: string) {
  if (!['review', 'vlm_review', 'review_vlm'].includes(nodeKey) || !modelConfigId) {
    return ''
  }
  const model = props.models.find((item) => item.id === Number(modelConfigId))
  if (!model) return ''
  const warning = '该模型未声明图片输入能力，视觉审查会明确进入 needs_review。'
  const explicit = model.provider_options.supports_image_input ?? model.provider_options.supports_vision
  if (explicit === true) return ''
  if (explicit === false) return warning
  return modelNameSupportsImageInput(model.model_name) ? '' : warning
}

function submit() {
  validationError.value = ''
  const missing = draft.node_bindings.find((item) => !item.model_config_id)
  if (missing) {
    validationError.value = `请先为节点 ${nodeMeta(missing.node_key)?.display_name ?? missing.node_key} 选择模型配置。`
    return
  }

  const node_bindings: AgentConfigNodeBinding[] = draft.node_bindings.map((item) => {
    const binding: AgentConfigNodeBinding = {
      node_key: item.node_key,
      model_config_id: Number(item.model_config_id),
    }
    if (item.temperature !== '') {
      binding.temperature = Number(item.temperature)
    }
    if (item.max_tokens !== '') {
      binding.max_tokens = Number(item.max_tokens)
    }
    return binding
  })

  emit(
    'save',
    {
      agent_id: Number(draft.agent_id),
      name: draft.name.trim(),
      description: draft.description.trim(),
      is_default: draft.is_default,
      status: draft.status,
      node_bindings,
    },
    draft.id,
  )
}
</script>

<template>
  <section class="page">
    <div class="page-head">
      <div>
        <h1>Agent 管理</h1>
        <p>Agent 和节点定义来自后端，前端根据 nodes 动态生成配置表单。</p>
      </div>
    </div>

    <div class="agent-grid">
      <EmptyState
        v-if="agents.length === 0"
        :icon="Bot"
        title="后端暂无 Agent"
        text="确认后端已登记 ppt_agent，并重新刷新工作台。"
      />
      <button v-for="agent in agents" :key="agent.id" class="agent-card" type="button" @click="reset(agent.id)">
        <div class="card-title">
          <strong>{{ agent.name }}</strong>
          <StatusBadge :tone="agent.status === 'active' ? 'green' : 'gray'" :label="agent.status" />
        </div>
        <span class="mono">{{ agent.agent_key }}</span>
        <p>{{ agent.description }}</p>
        <div class="node-pills">
          <span v-for="node in agent.nodes" :key="node.key">{{ node.display_name }}</span>
        </div>
      </button>
    </div>

    <div>
      <div class="section-title-row">
        <h2>Agent Config</h2>
        <button class="btn" type="button" :disabled="agents.length === 0" @click="reset()">
          <Plus :size="18" />
          新建配置
        </button>
      </div>

      <EmptyState
        v-if="agentConfigs.length === 0"
        :icon="Bot"
        title="还没有 Agent Config"
        text="为 PPTAgent 每个 requires_model 节点绑定 active Model Config。"
      />

      <div v-else class="list-stack">
        <button v-for="config in agentConfigs" :key="config.id" class="list-item tall" type="button" @click="edit(config)">
          <div>
            <strong>{{ config.name }}</strong>
            <span>{{ config.agent_name }} / {{ config.description || '无描述' }}</span>
            <NodeBindingList :config="config" :agents="agents" :models="models" />
          </div>
          <div class="row-actions">
            <StatusBadge v-if="config.is_default" tone="blue" label="默认" />
            <StatusBadge :tone="config.status === 'active' ? 'green' : 'gray'" :label="config.status" />
          </div>
        </button>
      </div>
    </div>

    <Teleport to="body">
      <div v-if="isEditorOpen" class="modal-backdrop" @click.self="closeEditor">
        <form class="modal-panel config-modal" role="dialog" aria-modal="true" @submit.prevent="submit">
          <div class="modal-head">
            <div>
              <h2>{{ draft.id ? '编辑 Agent Config' : '新建 Agent Config' }}</h2>
              <p>只允许绑定 active Model Config。</p>
            </div>
            <div class="modal-head-actions">
              <button v-if="draft.id" class="icon-btn danger" type="button" title="删除" @click="emit('delete', draft.id)">
                <Trash2 :size="18" />
              </button>
              <button class="icon-btn" type="button" title="关闭" @click="closeEditor">
                <X :size="18" />
              </button>
            </div>
          </div>

          <div class="modal-body">
            <label>
              Agent
              <select v-model="draft.agent_id" :disabled="Boolean(draft.id)" required>
                <option v-for="agent in agents" :key="agent.id" :value="String(agent.id)">
                  {{ agent.name }} / {{ agent.agent_key }}
                </option>
              </select>
            </label>

            <label>
              配置名称
              <input v-model="draft.name" required maxlength="64" placeholder="例如：PPTAgent 高质量配置" />
            </label>

            <label>
              描述
              <textarea v-model="draft.description" maxlength="255" placeholder="例如：大纲和审核用强模型，内容生成用均衡模型" />
            </label>

            <div class="subsection">
              <h3>节点模型绑定</h3>
              <div v-for="binding in draft.node_bindings" :key="binding.node_key" class="node-editor">
                <div>
                  <strong>{{ nodeMeta(binding.node_key)?.display_name ?? binding.node_key }}</strong>
                  <p>{{ nodeMeta(binding.node_key)?.description }}</p>
                </div>
                <label>
                  Model Config
                  <select v-model="binding.model_config_id" required>
                    <option value="">请选择 active 模型配置</option>
                    <option v-for="model in activeModels" :key="model.id" :value="String(model.id)">
                      {{ model.name }} / {{ model.provider }} / {{ model.model_name }}
                    </option>
                  </select>
                  <small v-if="bindingVisionWarning(binding.node_key, binding.model_config_id)" class="form-error">
                    {{ bindingVisionWarning(binding.node_key, binding.model_config_id) }}
                  </small>
                </label>
                <div class="form-grid">
                  <label>
                    Temperature 覆盖
                    <input v-model="binding.temperature" type="number" min="0" max="2" step="0.1" placeholder="沿用模型默认" />
                  </label>
                  <label>
                    Max tokens 覆盖
                    <input v-model="binding.max_tokens" type="number" min="1" step="1" placeholder="沿用模型默认" />
                  </label>
                </div>
              </div>
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

            <p v-if="validationError" class="form-error">{{ validationError }}</p>
          </div>

          <div class="modal-actions">
            <button class="btn ghost" type="button" @click="emit('goPpt')">去 PPTAgent</button>
            <button class="btn primary" type="submit" :disabled="saving || activeModels.length === 0">
              <Save :size="18" />
              保存 Agent Config
            </button>
          </div>
        </form>
      </div>
    </Teleport>
  </section>
</template>
