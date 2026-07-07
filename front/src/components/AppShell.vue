<script setup lang="ts">
import {
  Bot,
  ClipboardList,
  Database,
  FileClock,
  LayoutDashboard,
  LogOut,
  Presentation,
  RefreshCw,
} from '@lucide/vue'

import StatusBadge from '@/components/StatusBadge.vue'

defineProps<{
  activePage: string
  userLabel: string
  apiHealth: 'unknown' | 'ok' | 'down'
  loading: boolean
}>()

defineEmits<{
  navigate: [page: string]
  reload: []
  logout: []
}>()

const navItems = [
  { key: 'dashboard', label: '工作台', icon: LayoutDashboard },
  { key: 'models', label: 'Model Config', icon: Database },
  { key: 'agents', label: 'Agent 管理', icon: Bot },
  { key: 'ppt', label: 'PPTAgent', icon: Presentation },
  { key: 'logs', label: '请求/任务记录', icon: FileClock },
]
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <ClipboardList :size="24" />
        <div>
          <strong>Agent Task Platform</strong>
          <span>Workflow Console</span>
        </div>
      </div>

      <nav class="nav">
        <button
          v-for="item in navItems"
          :key="item.key"
          type="button"
          :class="{ active: activePage === item.key }"
          @click="$emit('navigate', item.key)"
        >
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
        </button>
      </nav>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="topbar-status">
          <StatusBadge
            :tone="apiHealth === 'ok' ? 'green' : apiHealth === 'down' ? 'red' : 'gray'"
            :label="apiHealth === 'ok' ? 'API 正常' : apiHealth === 'down' ? 'API 离线' : 'API 未知'"
          />
          <span>{{ userLabel }}</span>
        </div>
        <div class="topbar-actions">
          <button class="icon-btn" type="button" :disabled="loading" title="刷新工作台" @click="$emit('reload')">
            <RefreshCw :size="18" />
          </button>
          <button class="icon-btn" type="button" title="退出登录" @click="$emit('logout')">
            <LogOut :size="18" />
          </button>
        </div>
      </header>

      <slot />
    </main>
  </div>
</template>
