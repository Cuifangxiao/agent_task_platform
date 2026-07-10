<script setup lang="ts">
import { X } from '@lucide/vue'

import type { PreviewLightbox } from '../types'

defineProps<{ preview: PreviewLightbox | null }>()
defineEmits<{ close: [] }>()
</script>

<template>
  <div v-if="preview" class="preview-lightbox" role="dialog" aria-modal="true" @click.self="$emit('close')">
    <div class="preview-lightbox-panel">
      <div class="preview-lightbox-head">
        <div>
          <span>{{ preview.subtitle }}</span>
          <strong>{{ preview.title }}</strong>
        </div>
        <button class="icon-button" type="button" aria-label="Close preview" @click="$emit('close')">
          <X :size="18" />
        </button>
      </div>
      <div class="preview-lightbox-stage">
        <img :src="preview.src" :alt="preview.title" />
      </div>
    </div>
  </div>
</template>
