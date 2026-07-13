<script setup lang="ts">
import { reactive, ref } from 'vue'
import { Eye, EyeOff, FileChartColumn, Layers3, LogIn, Network, ShieldCheck, UserPlus } from '@lucide/vue'

const props = defineProps<{
  busy: boolean
}>()

const emit = defineEmits<{
  login: [username: string, password: string]
  register: [username: string, password: string]
}>()

const form = reactive({
  username: 'admin',
  password: '123456',
})
const error = ref('')
const passwordVisible = ref(false)

function submit() {
  error.value = ''
  if (!form.username.trim() || !form.password.trim()) {
    error.value = '请输入用户名和密码。'
    return
  }
  emit('login', form.username.trim(), form.password)
}

function register() {
  error.value = ''
  if (form.username.trim().length < 3 || form.password.length < 6) {
    error.value = '注册账号需要至少 3 位用户名和 6 位密码。'
    return
  }
  emit('register', form.username.trim(), form.password)
}
</script>

<template>
  <main class="login-screen">
    <section class="login-panel">
      <div class="login-copy">
        <div class="login-brand">
          <strong>Agent Task Platform</strong>
          <span>AI Workflow Console</span>
        </div>

        <div class="login-intro">
          <span class="page-eyebrow">一站式 Agent 工作台</span>
          <h1>把模型、工作流和生成任务放在一个清晰的控制台里。</h1>
          <p>完成配置后，即可通过 PPTAgent 发起真实任务，并持续查看每个阶段的运行状态。</p>
        </div>

        <div class="login-feature-list" aria-label="平台能力">
          <div class="login-feature">
            <span><Layers3 :size="21" /></span>
            <div><strong>统一模型配置</strong><p>集中管理 Provider、凭证和默认参数。</p></div>
          </div>
          <div class="login-feature">
            <span><Network :size="21" /></span>
            <div><strong>可视化节点绑定</strong><p>为 Agent 的每个节点选择合适的模型。</p></div>
          </div>
          <div class="login-feature">
            <span><FileChartColumn :size="21" /></span>
            <div><strong>完整 PPT 工作流</strong><p>从需求、大纲到视觉审查和导出。</p></div>
          </div>
        </div>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <div class="login-form-head">
          <span class="login-security"><ShieldCheck :size="17" /> 安全登录</span>
          <h2>欢迎回来</h2>
          <p>登录后继续管理您的 Agent 工作流。</p>
        </div>

        <div class="login-fields">
          <label>
            用户名
            <input v-model="form.username" autocomplete="username" placeholder="请输入用户名" />
          </label>
          <label>
            密码
            <span class="password-field">
              <input
                v-model="form.password"
                :type="passwordVisible ? 'text' : 'password'"
                autocomplete="current-password"
                placeholder="请输入密码"
              />
              <button
                type="button"
                :aria-label="passwordVisible ? '隐藏密码' : '显示密码'"
                @click="passwordVisible = !passwordVisible"
              >
                <EyeOff v-if="passwordVisible" :size="18" />
                <Eye v-else :size="18" />
              </button>
            </span>
          </label>
        </div>

        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="btn primary" type="submit" :disabled="props.busy">
          <LogIn :size="18" />
          登录工作台
        </button>
        <div class="login-register">
          <span>还没有账号？</span>
          <button type="button" :disabled="props.busy" @click="register">
            <UserPlus :size="16" />
            注册当前账号
          </button>
        </div>
      </form>
    </section>
  </main>
</template>
