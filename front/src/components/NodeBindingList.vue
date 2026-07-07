<script setup lang="ts">
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

function modelLabel(modelConfigId: number) {
  const model = props.models.find((item) => item.id === modelConfigId)
  return model?.name ?? `Model #${modelConfigId}`
}
</script>

<template>
  <div class="binding-list">
    <span v-for="binding in config.node_bindings" :key="binding.node_key">
      {{ nodeLabel(binding.node_key) }} -> {{ modelLabel(binding.model_config_id) }}
    </span>
  </div>
</template>
