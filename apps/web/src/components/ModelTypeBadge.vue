<script setup lang="ts">
import { computed } from 'vue'
import { Image as ImageIcon, MessageSquareText, ScanEye } from '@lucide/vue'

import { modelTypeLabel, normalizeModelType } from '@/features/models/modelPresentation'
import type { ModelType } from '@/types'

const props = defineProps<{
  modelType?: ModelType | string
}>()

const normalizedType = computed(() => normalizeModelType(props.modelType))
const label = computed(() => modelTypeLabel(normalizedType.value))
const icon = computed(() => {
  if (normalizedType.value === 'multimodal') return ScanEye
  if (normalizedType.value === 'image') return ImageIcon
  return MessageSquareText
})
</script>

<template>
  <span class="model-type-badge" :class="`model-type-badge--${normalizedType}`">
    <component :is="icon" :size="13" aria-hidden="true" />
    {{ label }}
  </span>
</template>

<style scoped>
.model-type-badge {
  display: inline-flex;
  width: fit-content;
  align-items: center;
  gap: 5px;
  border-radius: 999px;
  padding: 4px 8px;
  font-size: 12px;
  font-weight: 650;
  white-space: nowrap;
}

.model-type-badge--text {
  background: #eef2ff;
  color: #4338ca;
}

.model-type-badge--multimodal {
  background: #f3e8ff;
  color: #7e22ce;
}

.model-type-badge--image {
  background: #fff1f2;
  color: #be123c;
}
</style>
