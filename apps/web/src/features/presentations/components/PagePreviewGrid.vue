<script setup lang="ts">
import { Expand, Images, Loader2 } from '@lucide/vue'

import type { PptPagePreview, PreviewLightbox } from '../types'

const props = defineProps<{
  previews: PptPagePreview[]
  sources: Record<string, string>
  compact?: boolean
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
  <section class="preview-panel" :class="{ 'is-compact': props.compact }" aria-labelledby="page-preview-title">
    <div class="panel-head">
      <div>
        <span class="preview-kicker">OUTPUT</span>
        <h3 id="page-preview-title">页面预览</h3>
        <p>页面生成后会持续出现在这里，点击可查看大图。</p>
      </div>
      <span class="chat-note">{{ props.previews.length }} 页</span>
    </div>

    <div v-if="props.previews.length === 0" class="preview-empty">
      <Images :size="22" />
      <strong>预览正在准备</strong>
      <p>首个页面完成后会自动显示，无需刷新。</p>
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
          <span v-else class="slide-preview-placeholder"><Loader2 class="spin" :size="18" />正在读取预览</span>
          <span v-if="props.sources[preview.resource.resource_id]" class="slide-preview-action"><Expand :size="14" />查看大图</span>
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

.preview-panel.is-compact {
  gap: 12px;
  border: 0;
  border-radius: 0;
  padding: 0;
}

.preview-panel.is-compact .preview-grid {
  grid-template-columns: 1fr;
  gap: 12px;
}

.preview-panel.is-compact .preview-empty {
  min-height: 180px;
}

.preview-panel.is-compact .panel-head p {
  max-width: 260px;
}

.preview-panel h3 {
  margin-top: 4px;
  color: var(--text);
  font-size: 16px;
}

.preview-panel .panel-head p {
  margin-top: 4px;
  color: var(--muted);
  font-size: 12px;
}

.preview-kicker {
  color: var(--accent-strong);
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.12em;
}

.preview-panel .chat-note {
  flex: 0 0 auto;
  white-space: nowrap;
}

.preview-empty {
  min-height: 240px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  border: 1px dashed var(--line);
  border-radius: 8px;
  color: var(--muted);
  text-align: center;
}

.preview-empty strong {
  color: var(--text);
  font-size: 13px;
}

.preview-empty p {
  font-size: 12px;
}

.preview-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.preview-card {
  display: grid;
  gap: 8px;
}

.slide-preview-frame {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  display: grid;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #f2f5f6;
  padding: 0;
}

.slide-preview-frame:not(:disabled):hover {
  border-color: var(--accent);
  box-shadow: 0 8px 20px rgba(15, 35, 42, 0.08);
}

.slide-preview-thumb {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: contain;
  background: #fff;
}

.slide-preview-placeholder {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  color: var(--muted);
  font-size: 12px;
}

.slide-preview-action {
  position: absolute;
  right: 8px;
  bottom: 8px;
  display: inline-flex;
  gap: 5px;
  align-items: center;
  border-radius: 6px;
  background: rgba(17, 24, 39, 0.82);
  color: #fff;
  padding: 5px 7px;
  font-size: 10px;
  opacity: 0;
  transform: translateY(3px);
  transition: opacity 140ms ease, transform 140ms ease;
}

.slide-preview-frame:hover .slide-preview-action,
.slide-preview-frame:focus-visible .slide-preview-action {
  opacity: 1;
  transform: translateY(0);
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

@media (max-width: 760px) {
  .preview-grid { grid-template-columns: 1fr; }
}

@media (prefers-reduced-motion: reduce) {
  .slide-preview-action { transition: none; }
}
</style>
