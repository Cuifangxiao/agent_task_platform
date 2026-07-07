<script setup lang="ts">
defineProps<{
  tone?: 'green' | 'red' | 'blue' | 'gray' | 'amber'
  label: string
}>()
</script>

<template>
  <span class="badge" :class="`tone-${tone ?? 'gray'}`">{{ label }}</span>
</template>
