<script setup lang="ts">
import { Bot, Database, Presentation, Route } from '@lucide/vue'

import type { AgentConfig, AgentDefinition, ModelConfig } from '@/types'

defineProps<{
  models: ModelConfig[]
  agents: AgentDefinition[]
  agentConfigs: AgentConfig[]
  pptConfigs: AgentConfig[]
}>()

defineEmits<{
  navigate: [page: string]
}>()
</script>

<template>
  <section class="page">
    <div class="page-head">
      <div>
        <h1>工作台</h1>
        <p>从模型资源到 Agent 节点绑定，再到 PPT 生成任务的当前状态。</p>
      </div>
    </div>

    <div class="metrics">
      <button class="metric" type="button" @click="$emit('navigate', 'models')">
        <Database :size="22" />
        <span>Model Config</span>
        <strong>{{ models.length }}</strong>
      </button>
      <button class="metric" type="button" @click="$emit('navigate', 'agents')">
        <Bot :size="22" />
        <span>Agent</span>
        <strong>{{ agents.length }}</strong>
      </button>
      <button class="metric" type="button" @click="$emit('navigate', 'agents')">
        <Route :size="22" />
        <span>Agent Config</span>
        <strong>{{ agentConfigs.length }}</strong>
      </button>
      <button class="metric" type="button" @click="$emit('navigate', 'ppt')">
        <Presentation :size="22" />
        <span>PPTAgent Config</span>
        <strong>{{ pptConfigs.length }}</strong>
      </button>
    </div>

    <div class="workflow">
      <div>
        <span>1</span>
        <strong>创建 Model Config</strong>
        <p>把 provider、模型名、凭证和默认参数保存成可复用资源。</p>
      </div>
      <div>
        <span>2</span>
        <strong>配置 Agent 节点</strong>
        <p>从后端 Agent schema 动态渲染节点，为每个需要模型的节点绑定配置。</p>
      </div>
      <div>
        <span>3</span>
        <strong>运行 PPTAgent</strong>
        <p>选择 active PPTAgent Config，填写生成描述，也可以附加文件或文字资料。</p>
      </div>
    </div>
  </section>
</template>
