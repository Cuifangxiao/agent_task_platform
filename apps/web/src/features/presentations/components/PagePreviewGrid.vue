<script setup lang="ts">
import { Expand, Images } from '@lucide/vue'

import type { PptPagePreview, PreviewLightbox } from '../types'

const props = defineProps<{
  previews: PptPagePreview[]
  sources: Record<string, string>
}>()
const emit = defineEmits<{ open: [preview: PreviewLightbox] }>()

function title(preview: PptPagePreview, index: number) {
  return preview.page?.title || preview.resource.name || `第 ${preview.page?.page_index ?? index + 1} 页`
}

function open(preview: PptPagePreview, index: number) {
  emit('open', {
    src: props.sources[preview.resource.resource_id] || '',
    title: title(preview, index),
    subtitle: preview.page?.page_index ? `第 ${preview.page.page_index} 页` : '页面预览',
  })
}
</script>

<template>
  <section class="preview-panel" aria-labelledby="page-preview-title">
    <div class="panel-head">
      <div>
        <span class="chat-shell-kicker">Page previews</span>
        <h3 id="page-preview-title">页面预览</h3>
      </div>
      <span class="chat-note">{{ props.previews.length }} 页</span>
    </div>

    <div v-if="props.previews.length === 0" class="preview-empty">
      <Images :size="22" />
      <p>收到 PAGE_PREVIEW 资源后会显示在这里。</p>
    </div>

    <div v-else class="preview-grid">
      <article v-for="(preview, index) in props.previews" :key="preview.resource.resource_id" class="preview-card">
        <button
          class="slide-preview-frame"
          type="button"
          :disabled="!props.sources[preview.resource.resource_id]"
          @click="open(preview, index)"
        >
          <img
            v-if="props.sources[preview.resource.resource_id]"
            class="slide-preview-thumb"
            :src="props.sources[preview.resource.resource_id]"
            :alt="title(preview, index)"
          />
          <span v-else class="slide-preview-placeholder">正在读取预览…</span>
          <span v-if="props.sources[preview.resource.resource_id]" class="slide-preview-action"><Expand :size="14" />放大</span>
        </button>
        <div>
          <strong>{{ title(preview, index) }}</strong>
          <span v-if="preview.page?.page_index">第 {{ preview.page.page_index }} 页</span>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.preview-panel {
  display: grid;
  gap: 14px;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 18px;
}

.preview-panel h3 {
  margin-top: 4px;
  color: var(--text);
  font-size: 16px;
}

.preview-empty {
  min-height: 160px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  border: 1px dashed var(--line);
  border-radius: 8px;
  color: var(--muted);
  text-align: center;
}

.preview-grid {
  display: grid;
  gap: 14px;
}

.preview-card {
  display: grid;
  gap: 8px;
}

.preview-card > div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--muted);
  font-size: 12px;
}

.preview-card strong {
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
