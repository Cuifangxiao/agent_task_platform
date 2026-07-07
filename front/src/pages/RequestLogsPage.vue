<script setup lang="ts">
import { FileClock } from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
import type { RequestLogEntry } from '@/types'

defineProps<{
  logs: RequestLogEntry[]
}>()
</script>

<template>
  <section class="page">
    <div class="page-head">
      <div>
        <h1>请求/任务记录</h1>
        <p>用于回溯页面发起过的关键请求和流式任务，不展示原始密钥或大段 JSON。</p>
      </div>
    </div>

    <EmptyState
      v-if="logs.length === 0"
      :icon="FileClock"
      title="暂无记录"
      text="登录、保存配置或生成 PPT 后，这里会出现最近的请求摘要。"
    />

    <table v-else class="data-table">
      <thead>
        <tr>
          <th>时间</th>
          <th>方法</th>
          <th>路径</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="log in logs" :key="log.id">
          <td>{{ log.time }}</td>
          <td>{{ log.method }}</td>
          <td class="mono">{{ log.path }}</td>
          <td>{{ log.status }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
