<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ArrowRight, Bot, Network, Plus, Save, Trash2, X } from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
import NodeBindingList from '@/components/NodeBindingList.vue'
import SearchableModelSelect from '@/components/SearchableModelSelect.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { modelTypeLabel, normalizeModelType } from '@/features/models/modelPresentation'
import type { AgentConfig, AgentConfigNodeBinding, AgentConfigPayload, AgentDefinition, ModelConfig, Status } from '@/types'

const props = defineProps<{
  agents: AgentDefinition[]
  models: ModelConfig[]
  agentConfigs: AgentConfig[]
  saving: boolean
}>()

const emit = defineEmits<{
  save: [payload: AgentConfigPayload & { agent_id?: number }, id: number | undefined, callbacks: FormActionCallbacks]
  delete: [id: number, callbacks: FormActionCallbacks]
  goPpt: []
}>()

interface FormActionCallbacks {
  onError: (message: string) => void
  onSuccess?: (message?: string) => void
}

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
const formError = ref('')
const isEditorOpen = ref(false)
const activeModels = computed(() => props.models.filter((item) => (
  item.status === 'active' && normalizeModelType(item.model_type) !== 'image'
)))
const activeModelOptions = computed(() => activeModels.value.map((model) => ({
  value: String(model.id),
  label: model.name,
  description: `${model.provider} / ${model.model_name}`,
  provider: model.provider,
  modelType: normalizeModelType(model.model_type),
  group: modelTypeLabel(model.model_type),
})))
const selectedAgent = computed(() => props.agents.find((item) => item.id === Number(draft.agent_id)) ?? null)
const requiredNodes = computed(() => selectedAgent.value?.nodes.filter((node) => node.requires_model) ?? [])
const requiredNodesByKey = computed(() => new Map(requiredNodes.value.map((node) => [node.key, node])))
const activeAgents = computed(() => props.agents.filter((item) => item.status === 'active'))
const activeConfigs = computed(() => props.agentConfigs.filter((item) => item.status === 'active'))
const bindingCount = computed(() => props.agentConfigs.reduce((total, item) => total + item.node_bindings.length, 0))

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
  formError.value = ''
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
  formError.value = ''
  Object.assign(draft, emptyDraft())
  draft.agent_id = String(agentId ?? props.agents[0]?.id ?? '')
  syncNodeBindings()
  isEditorOpen.value = true
}

function closeEditor() {
  isEditorOpen.value = false
  formError.value = ''
}

function nodeMeta(nodeKey: string) {
  return requiredNodesByKey.value.get(nodeKey)
}

function submit() {
  formError.value = ''
  const missing = draft.node_bindings.find((item) => !item.model_config_id)
  if (missing) {
    formError.value = `请先为节点 ${nodeMeta(missing.node_key)?.display_name ?? missing.node_key} 选择模型配置。`
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

  emit('save', {
    agent_id: Number(draft.agent_id),
    name: draft.name.trim(),
    description: draft.description.trim(),
    is_default: draft.is_default,
    status: draft.status,
    node_bindings,
  }, draft.id, {
    onError: (message) => {
      formError.value = message
    },
    onSuccess: closeEditor,
  })
}
</script>

<template>
  <section class="page management-page agent-management-page">
    <header class="page-head management-head">
      <div class="page-title-block">
        <span class="page-eyebrow">工作流与节点</span>
        <h2>编排 Agent 工作流</h2>
        <p>查看后端登记的 Agent 工作流，并为每个需要推理的节点绑定合适的模型配置。</p>
      </div>
      <button class="btn primary" type="button" :disabled="agents.length === 0" @click="reset()">
        <Plus :size="18" />
        新建 Agent 配置
      </button>
    </header>

    <div class="page-summary" aria-label="Agent 配置概览">
      <div><span>可用 Agent</span><strong>{{ activeAgents.length }}</strong></div>
      <div><span>启用配置</span><strong>{{ activeConfigs.length }}</strong></div>
      <div class="summary-wide"><span>模型绑定</span><strong>{{ bindingCount }} 个节点</strong></div>
    </div>

    <section class="content-panel" aria-labelledby="agent-definition-title">
      <div class="content-panel-head">
        <div>
          <h3 id="agent-definition-title">Agent 定义</h3>
          <p>定义和节点由后端维护；选择一个 Agent 可快速创建配置。</p>
        </div>
        <span class="panel-count">{{ agents.length }} 个工作流</span>
      </div>

      <EmptyState
        v-if="agents.length === 0"
        :icon="Bot"
        title="后端暂无 Agent"
        text="确认后端已登记 ppt_agent，并重新刷新工作台。"
      />

      <div v-else class="agent-grid">
        <button v-for="agent in agents" :key="agent.id" class="agent-card" type="button" @click="reset(agent.id)">
          <span class="agent-card-icon"><Network :size="24" /></span>
          <span class="agent-card-main">
            <span class="card-title">
              <strong>{{ agent.name }}</strong>
              <StatusBadge :tone="agent.status === 'active' ? 'green' : 'gray'" :label="agent.status === 'active' ? '可用' : '停用'" />
            </span>
            <span class="mono agent-key">{{ agent.agent_key }}</span>
            <p>{{ agent.description }}</p>
          </span>
          <span class="agent-card-nodes">
            <small>{{ agent.nodes.filter((node) => node.requires_model).length }} 个推理节点</small>
            <span class="node-pills">
              <span v-for="node in agent.nodes" :key="node.key">{{ node.display_name }}</span>
            </span>
          </span>
          <span class="agent-card-action">创建配置 <ArrowRight :size="16" /></span>
        </button>
      </div>
    </section>

    <section class="content-panel" aria-labelledby="agent-config-title">
      <div class="content-panel-head">
        <div>
          <h3 id="agent-config-title">Agent 配置</h3>
          <p>配置决定每个节点实际使用的模型，并可在 PPTAgent 中直接选择。</p>
        </div>
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

      <div v-else class="config-list agent-config-list">
        <button v-for="config in agentConfigs" :key="config.id" class="config-row agent-config-list-item" type="button" @click="edit(config)">
          <span class="config-row-icon agent-config-icon"><Bot :size="22" /></span>
          <span class="agent-config-content">
            <span class="config-row-title">
              <strong>{{ config.name }}</strong>
              <StatusBadge v-if="config.is_default" tone="blue" label="默认" />
            </span>
            <span>{{ config.agent_name }} · {{ config.description || '无描述' }}</span>
            <NodeBindingList :config="config" :agents="agents" :models="models" />
          </span>
          <span class="agent-config-status">
            <StatusBadge :tone="config.status === 'active' ? 'green' : 'gray'" :label="config.status === 'active' ? '已启用' : '已停用'" />
            <ArrowRight :size="17" />
          </span>
        </button>
      </div>
    </section>

    <Teleport to="body">
      <div v-if="isEditorOpen" class="modal-backdrop" @click.self="closeEditor">
        <form class="modal-panel config-modal" role="dialog" aria-modal="true" @submit.prevent="submit">
          <div class="modal-head">
            <div>
              <h2>{{ draft.id ? '编辑 Agent 配置' : '新建 Agent 配置' }}</h2>
              <p>仅可绑定已启用的模型配置。</p>
              <p v-if="formError" class="form-error modal-feedback" role="alert">{{ formError }}</p>
            </div>
            <div class="modal-head-actions">
              <button
                v-if="draft.id"
                class="icon-btn danger"
                type="button"
                title="删除"
                @click="emit('delete', draft.id, { onError: (message) => { formError = message }, onSuccess: closeEditor })"
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
                <div class="model-picker-field">
                  <span>Model Config</span>
                  <SearchableModelSelect
                    v-model="binding.model_config_id"
                    :options="activeModelOptions"
                    placeholder="请选择 active 模型配置"
                  />
                </div>
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

          </div>

          <div class="modal-actions">
            <button class="btn ghost" type="button" @click="emit('goPpt')">去 PPTAgent</button>
            <button class="btn primary" type="submit" :disabled="saving || activeModels.length === 0">
              <Save :size="18" />
              保存 Agent 配置
            </button>
          </div>
        </form>
      </div>
    </Teleport>
  </section>
</template>
