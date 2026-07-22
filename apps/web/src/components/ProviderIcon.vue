<script setup lang="ts">
import { Box, Moon, Sparkles } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import deepseekIconUrl from '@/assets/providers/deepseek.svg?url'
import openaiIconUrl from '@/assets/providers/openai.svg?url'
import qwenIconUrl from '@/assets/providers/qwen.png?url'
import { providerBrandLabel, resolveProviderBrand } from '@/features/models/modelPresentation'

const props = withDefaults(defineProps<{
  provider: string
  size?: number
}>(), {
  size: 32,
})

const brand = computed(() => resolveProviderBrand(props.provider))
const label = computed(() => providerBrandLabel(props.provider))
const imageLoadFailed = ref(false)
const iconSrc = computed(() => {
  if (brand.value === 'openai') return openaiIconUrl
  if (brand.value === 'qwen') return qwenIconUrl
  if (brand.value === 'deepseek') return deepseekIconUrl
  return ''
})

watch(brand, () => {
  imageLoadFailed.value = false
})
</script>

<template>
  <span
    class="provider-icon"
    :class="`provider-icon--${brand}`"
    :style="{ width: `${size}px`, height: `${size}px` }"
    role="img"
    :aria-label="`${label} 图标`"
    :title="label"
  >
    <img
      v-if="iconSrc && !imageLoadFailed"
      :src="iconSrc"
      alt=""
      aria-hidden="true"
      @error="imageLoadFailed = true"
    />
    <Sparkles
      v-else-if="brand === 'gemini'"
      :size="Math.max(15, Math.round(size * 0.6))"
      aria-hidden="true"
    />
    <Moon
      v-else-if="brand === 'kimi'"
      :size="Math.max(15, Math.round(size * 0.6))"
      aria-hidden="true"
    />
    <Box v-else :size="Math.max(14, Math.round(size * 0.58))" aria-hidden="true" />
  </span>
</template>

<style scoped>
.provider-icon {
  display: inline-grid;
  place-items: center;
  flex: 0 0 auto;
  border: 1px solid rgba(15, 23, 42, 0.1);
  border-radius: 9px;
  background: #fff;
  color: #64748b;
}

.provider-icon img {
  display: block;
  width: 70%;
  height: 70%;
  object-fit: contain;
}

.provider-icon--gemini {
  color: #5b5bd6;
  background: linear-gradient(145deg, #f7f2ff, #eef6ff);
}

.provider-icon--kimi {
  color: #1769e0;
  background: linear-gradient(145deg, #edf5ff, #f5f8ff);
}

</style>
