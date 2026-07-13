<script setup lang="ts">
import {
  Bell,
  ChevronDown,
  ChevronRight,
  Clock3,
  FileChartColumn,
  House,
  Layers3,
  LogOut,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
} from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import StatusBadge from '@/components/StatusBadge.vue'

const props = defineProps<{
  activePage: string
  userLabel: string
  apiHealth: 'unknown' | 'ok' | 'down'
  loading: boolean
}>()

const emit = defineEmits<{
  navigate: [page: string]
  reload: []
  logout: []
}>()

const navItems = [
  { key: 'dashboard', label: '工作台', icon: House },
  { key: 'models', label: '模型配置', icon: Layers3 },
  { key: 'agents', label: 'Agent 管理', icon: Network },
  { key: 'ppt', label: 'PPTAgent', icon: FileChartColumn },
  { key: 'logs', label: '运行记录', icon: Clock3 },
]

const sidebarCollapsed = ref(false)
const notificationOpen = ref(false)
const accountOpen = ref(false)
const pageTitle = computed(() => navItems.find((item) => item.key === props.activePage)?.label ?? '工作台')
const avatarLabel = computed(() => props.userLabel.trim().charAt(0).toUpperCase() || 'A')

watch(() => props.activePage, () => {
  notificationOpen.value = false
  accountOpen.value = false
})

function navigate(page: string) {
  notificationOpen.value = false
  accountOpen.value = false
  emit('navigate', page)
}

function toggleNotifications() {
  notificationOpen.value = !notificationOpen.value
  accountOpen.value = false
}

function toggleAccount() {
  accountOpen.value = !accountOpen.value
  notificationOpen.value = false
}
</script>

<template>
  <div class="app-shell" :class="{ 'sidebar-is-collapsed': sidebarCollapsed }">
    <aside class="sidebar">
      <div class="brand">
        <strong>Agent Task Platform</strong>
        <span>AI Workflow Console</span>
      </div>

      <nav class="nav" aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.key"
          type="button"
          :class="{ active: activePage === item.key }"
          :aria-current="activePage === item.key ? 'page' : undefined"
          :title="sidebarCollapsed ? item.label : undefined"
          @click="navigate(item.key)"
        >
          <component :is="item.icon" :size="21" :stroke-width="1.8" />
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <button
        class="sidebar-collapse"
        type="button"
        :aria-label="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
        @click="sidebarCollapsed = !sidebarCollapsed"
      >
        <PanelLeftOpen v-if="sidebarCollapsed" :size="18" />
        <PanelLeftClose v-else :size="18" />
        <span>{{ sidebarCollapsed ? '展开' : '收起' }}</span>
      </button>
    </aside>

    <main class="main">
      <header class="topbar">
        <h1>{{ pageTitle }}</h1>

        <div class="topbar-actions">
          <StatusBadge
            :tone="apiHealth === 'ok' ? 'green' : apiHealth === 'down' ? 'red' : 'gray'"
            :label="apiHealth === 'ok' ? 'API 正常' : apiHealth === 'down' ? 'API 离线' : 'API 检测中'"
          />

          <div class="topbar-popover">
            <button
              class="topbar-icon-button"
              type="button"
              aria-label="查看通知"
              :aria-expanded="notificationOpen"
              @click="toggleNotifications"
            >
              <Bell :size="21" :stroke-width="1.8" />
              <span class="notification-dot" aria-hidden="true" />
            </button>
            <div v-if="notificationOpen" class="popover-panel notification-panel">
              <strong>通知</strong>
              <p>系统运行正常，暂无需要处理的异常。</p>
              <button type="button" @click="navigate('logs')">
                查看运行记录
                <ChevronRight :size="15" />
              </button>
            </div>
          </div>

          <div class="topbar-popover">
            <button
              class="account-trigger"
              type="button"
              :aria-expanded="accountOpen"
              aria-label="打开用户菜单"
              @click="toggleAccount"
            >
              <span class="account-avatar">{{ avatarLabel }}</span>
              <span class="account-name">{{ userLabel }}</span>
              <ChevronDown :size="16" :class="{ rotated: accountOpen }" />
            </button>
            <div v-if="accountOpen" class="popover-panel account-panel">
              <span class="account-panel-label">当前账号</span>
              <strong>{{ userLabel }}</strong>
              <button type="button" :disabled="loading" @click="$emit('reload')">
                <RefreshCw :size="16" />
                刷新数据
              </button>
              <button type="button" class="danger-action" @click="$emit('logout')">
                <LogOut :size="16" />
                退出登录
              </button>
            </div>
          </div>
        </div>
      </header>

      <div class="page-stage">
        <slot />
      </div>
    </main>
  </div>
</template>
