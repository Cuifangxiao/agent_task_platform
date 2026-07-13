<script setup lang="ts">
import { computed, ref } from 'vue'
import { CircleCheck, CircleX, FileClock, RotateCcw, Search } from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
import type { RequestLogEntry } from '@/types'

const props = defineProps<{
  logs: RequestLogEntry[]
}>()

const searchQuery = ref('')
const methodFilter = ref('all')
const statusFilter = ref<'all' | 'success' | 'error'>('all')

const successCount = computed(() => props.logs.filter((item) => isSuccessful(item.status)).length)
const errorCount = computed(() => props.logs.length - successCount.value)
const methods = computed(() => [...new Set(props.logs.map((item) => item.method.toUpperCase()))].sort())
const filteredLogs = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return props.logs.filter((log) => {
    const matchesQuery = !query || [log.path, log.note, log.method, String(log.status)]
      .some((value) => String(value ?? '').toLowerCase().includes(query))
    const matchesMethod = methodFilter.value === 'all' || log.method.toUpperCase() === methodFilter.value
    const matchesStatus = statusFilter.value === 'all'
      || (statusFilter.value === 'success' ? isSuccessful(log.status) : !isSuccessful(log.status))
    return matchesQuery && matchesMethod && matchesStatus
  })
})

function isSuccessful(status: number | string) {
  const code = Number(status)
  return Number.isFinite(code) && code >= 200 && code < 400
}

function resetFilters() {
  searchQuery.value = ''
  methodFilter.value = 'all'
  statusFilter.value = 'all'
}
</script>

<template>
  <section class="page management-page request-logs-page">
    <header class="page-head management-head">
      <div class="page-title-block">
        <span class="page-eyebrow">请求与任务</span>
        <h2>查看系统运行轨迹</h2>
        <p>回溯页面发起的关键请求和流式任务摘要，不展示原始密钥或大段 JSON。</p>
      </div>
    </header>

    <div class="page-summary logs-summary" aria-label="运行记录概览">
      <div><span>记录总数</span><strong>{{ logs.length }}</strong></div>
      <div><span>成功</span><strong class="summary-success">{{ successCount }}</strong></div>
      <div><span>异常</span><strong :class="{ 'summary-danger': errorCount > 0 }">{{ errorCount }}</strong></div>
    </div>

    <EmptyState
      v-if="logs.length === 0"
      :icon="FileClock"
      title="暂无运行记录"
      text="登录、保存配置或生成 PPT 后，这里会出现最近的请求摘要。"
    />

    <section v-else class="content-panel logs-panel" aria-labelledby="request-log-title">
      <div class="content-panel-head logs-toolbar">
        <div>
          <h3 id="request-log-title">最近请求</h3>
          <p>共显示 {{ filteredLogs.length }} 条结果，最新记录排在最前。</p>
        </div>
        <label class="search-field logs-search">
          <Search :size="17" />
          <input v-model="searchQuery" type="search" placeholder="搜索路径、说明或状态" />
        </label>
      </div>

      <div class="logs-filter-row" aria-label="运行记录筛选">
        <label>
          请求方法
          <select v-model="methodFilter">
            <option value="all">全部方法</option>
            <option v-for="method in methods" :key="method" :value="method">{{ method }}</option>
          </select>
        </label>
        <label>
          请求状态
          <select v-model="statusFilter">
            <option value="all">全部状态</option>
            <option value="success">成功</option>
            <option value="error">异常</option>
          </select>
        </label>
        <button class="btn ghost logs-reset" type="button" @click="resetFilters">
          <RotateCcw :size="16" />
          重置筛选
        </button>
      </div>

      <div v-if="filteredLogs.length" class="table-scroll">
        <table class="data-table logs-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>方法</th>
              <th>请求路径</th>
              <th>说明</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="log in filteredLogs" :key="log.id">
              <td><time>{{ log.time }}</time></td>
              <td><span class="method-badge" :class="`is-${log.method.toLowerCase()}`">{{ log.method }}</span></td>
              <td class="mono log-path">{{ log.path }}</td>
              <td class="log-note">{{ log.note || '—' }}</td>
              <td>
                <span class="log-status" :class="isSuccessful(log.status) ? 'is-success' : 'is-error'">
                  <CircleCheck v-if="isSuccessful(log.status)" :size="16" />
                  <CircleX v-else :size="16" />
                  {{ log.status }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-else class="filtered-empty">
        <Search :size="22" />
        <strong>没有匹配的运行记录</strong>
        <span>调整搜索词或重置筛选后再试。</span>
      </div>
    </section>
  </section>
</template>
