<script setup lang="ts">
import { computed } from 'vue'

import ModelTypeBadge from '@/components/ModelTypeBadge.vue'
import ProviderIcon from '@/components/ProviderIcon.vue'
import type { AgentConfig, AgentDefinition, ModelConfig } from '@/types'

const props = defineProps<{
  config: AgentConfig
  agents: AgentDefinition[]
  models: ModelConfig[]
}>()

function nodeLabel(nodeKey: string) {
  const agent = props.agents.find((item) => item.id === props.config.agent_id)
  return agent?.nodes.find((node) => node.key === nodeKey)?.display_name ?? nodeKey
}

const bindingViews = computed(() => props.config.node_bindings.map((binding) => {
  const model = props.models.find((item) => item.id === binding.model_config_id) ?? null
  return {
    binding,
    model,
    modelLabel: model?.name ?? `Model #${binding.model_config_id}`,
    nodeLabel: nodeLabel(binding.node_key),
  }
}))
</script>

<template>
  <div class="binding-list">
    <div v-for="item in bindingViews" :key="item.binding.node_key" class="binding-item">
      <span class="binding-node-label">{{ item.nodeLabel }}</span>
      <div class="binding-model-row">
        <ProviderIcon v-if="item.model" :provider="item.model.provider" :size="22" />
        <div class="binding-model-copy">
          <strong>{{ item.modelLabel }}</strong>
          <small v-if="item.model">{{ item.model.provider }} · {{ item.model.model_name }}</small>
          <small v-else>模型配置不可用</small>
        </div>
      </div>
      <ModelTypeBadge v-if="item.model" class="binding-type-badge" :model-type="item.model.model_type" />
    </div>
  </div>
</template>
