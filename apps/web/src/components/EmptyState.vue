<script setup lang="ts">
import type { Component } from 'vue'

defineProps<{
  icon: Component
  title: string
  text: string
  action?: string
}>()

defineEmits<{
  action: []
}>()
</script>

<template>
  <div class="empty">
    <component :is="icon" :size="30" />
    <strong>{{ title }}</strong>
    <p>{{ text }}</p>
    <button v-if="action" class="btn primary" type="button" @click="$emit('action')">{{ action }}</button>
  </div>
</template>
