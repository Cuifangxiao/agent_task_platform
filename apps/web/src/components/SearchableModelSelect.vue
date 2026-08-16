<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { Check, ChevronDown, Search } from '@lucide/vue'

import ModelTypeBadge from '@/components/ModelTypeBadge.vue'
import ProviderIcon from '@/components/ProviderIcon.vue'
import type { ModelType } from '@/types'

interface ModelSelectOption {
  value: string
  label: string
  description: string
  provider?: string
  modelType?: ModelType | string
  group?: string
}

const props = withDefaults(defineProps<{
  modelValue: string
  options: ModelSelectOption[]
  placeholder?: string
  searchPlaceholder?: string
  disabled?: boolean
}>(), {
  placeholder: '请选择模型',
  searchPlaceholder: '搜索名称、Provider 或模型',
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const MAX_VISIBLE_OPTIONS = 60
const root = ref<HTMLElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)
const isOpen = ref(false)
const query = ref('')
const listboxId = `model-select-${useId()}`

const selectedOption = computed(() => props.options.find((option) => option.value === props.modelValue) ?? null)
const matchingOptions = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  if (!needle) return props.options
  return props.options.filter((option) => (
    `${option.label} ${option.description} ${option.provider ?? ''} ${option.group ?? ''}`
      .toLocaleLowerCase()
      .includes(needle)
  ))
})
const visibleOptions = computed(() => matchingOptions.value.slice(0, MAX_VISIBLE_OPTIONS))
const hiddenOptionCount = computed(() => Math.max(0, matchingOptions.value.length - visibleOptions.value.length))

watch(isOpen, (open) => {
  if (open) {
    document.addEventListener('pointerdown', closeFromOutside)
    void nextTick(() => searchInput.value?.focus({ preventScroll: true }))
  } else {
    document.removeEventListener('pointerdown', closeFromOutside)
    query.value = ''
  }
})

onBeforeUnmount(() => document.removeEventListener('pointerdown', closeFromOutside))

function toggle() {
  if (!props.disabled) isOpen.value = !isOpen.value
}

function closeFromOutside(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) isOpen.value = false
}

function select(option: ModelSelectOption) {
  emit('update:modelValue', option.value)
  isOpen.value = false
}
</script>

<template>
  <div ref="root" class="searchable-model-select" @keydown.esc="isOpen = false">
    <button
      class="model-select-trigger"
      :class="{ 'is-open': isOpen, 'has-value': selectedOption }"
      type="button"
      role="combobox"
      aria-haspopup="listbox"
      :aria-expanded="isOpen"
      :aria-controls="listboxId"
      :disabled="disabled"
      @click="toggle"
    >
      <ProviderIcon v-if="selectedOption?.provider" :provider="selectedOption.provider" :size="32" />
      <span class="model-select-trigger-copy">
        <strong>{{ selectedOption?.label || placeholder }}</strong>
        <small v-if="selectedOption">{{ selectedOption.description }}</small>
      </span>
      <ModelTypeBadge v-if="selectedOption?.modelType" :model-type="selectedOption.modelType" />
      <ChevronDown class="model-select-chevron" :size="17" />
    </button>

    <div v-if="isOpen" class="model-select-popover">
      <label class="model-select-search">
        <Search :size="16" />
        <input
          ref="searchInput"
          v-model="query"
          type="search"
          :placeholder="searchPlaceholder"
          @keydown.enter.prevent
        />
      </label>

      <div :id="listboxId" class="model-select-options" role="listbox">
        <button
          v-for="option in visibleOptions"
          :key="option.value"
          class="model-select-option"
          :class="{ 'is-selected': option.value === modelValue }"
          type="button"
          role="option"
          :aria-selected="option.value === modelValue"
          @click="select(option)"
        >
          <ProviderIcon v-if="option.provider" :provider="option.provider" :size="30" />
          <span class="model-select-option-copy">
            <strong>{{ option.label }}</strong>
            <small>{{ option.description }}</small>
          </span>
          <ModelTypeBadge v-if="option.modelType" :model-type="option.modelType" />
          <Check v-if="option.value === modelValue" :size="17" />
        </button>

        <p v-if="visibleOptions.length === 0" class="model-select-empty">没有匹配的模型。</p>
      </div>
      <p v-if="hiddenOptionCount" class="model-select-hint">
        还有 {{ hiddenOptionCount }} 项，请输入关键词缩小范围。
      </p>
    </div>
  </div>
</template>

<style scoped>
.searchable-model-select {
  position: relative;
  min-width: 0;
}

.model-select-trigger {
  width: 100%;
  min-height: 44px;
  display: flex;
  align-items: center;
  gap: 9px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  color: var(--text);
  padding: 6px 10px;
  text-align: left;
}

.model-select-trigger:hover,
.model-select-trigger.is-open {
  border-color: var(--accent);
}

.model-select-trigger:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--accent) 18%, transparent);
  outline-offset: 1px;
}

.model-select-trigger-copy,
.model-select-option-copy {
  min-width: 0;
  display: grid;
  gap: 2px;
  flex: 1;
}

.model-select-trigger-copy strong,
.model-select-trigger-copy small,
.model-select-option strong,
.model-select-option small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-select-trigger-copy strong,
.model-select-option strong {
  font-size: 13px;
}

.model-select-trigger-copy small,
.model-select-option small {
  color: var(--muted);
  font-size: 11px;
  font-weight: 400;
}

.model-select-chevron {
  flex: 0 0 auto;
  color: var(--muted);
  transition: transform 140ms ease;
}

.is-open .model-select-chevron {
  transform: rotate(180deg);
}

.model-select-popover {
  position: relative;
  z-index: 4;
  display: grid;
  gap: 8px;
  margin-top: 6px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: #fff;
  padding: 8px;
  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.14);
}

.model-select-search {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--muted);
}

.model-select-search input {
  min-height: 38px;
  padding: 7px 9px;
}

.model-select-options {
  max-height: 264px;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.model-select-option {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 9px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--text);
  padding: 8px;
  text-align: left;
}

.model-select-option:hover,
.model-select-option.is-selected {
  background: var(--accent-soft);
}

.model-select-option > svg:last-child {
  flex: 0 0 auto;
  color: var(--accent-strong);
}

.model-select-empty,
.model-select-hint {
  color: var(--muted);
  font-size: 12px;
  text-align: center;
}

.model-select-empty {
  padding: 18px 8px;
}

.model-select-hint {
  border-top: 1px solid var(--line);
  padding: 7px 6px 1px;
}
</style>
