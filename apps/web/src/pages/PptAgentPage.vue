<script setup lang="ts">
import PresentationWorkspace from '@/features/presentations/components/PresentationWorkspace.vue'
import type { PresentationApi } from '@/features/presentations/api'
import type { AgentConfig } from '@/types'

const props = defineProps<{
  configs: AgentConfig[]
  api: PresentationApi
}>()
</script>

<template>
  <PresentationWorkspace :configs="props.configs" :api="props.api" />
</template>
