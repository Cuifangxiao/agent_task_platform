<script setup lang="ts">
import { reactive, ref } from 'vue'
import { LogIn, UserPlus } from '@lucide/vue'

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
        <span>Agent Task Platform</span>
        <h1>配置 Agent 节点模型，生成可下载的 PPT。</h1>
        <p>登录后管理 Model Config、Agent Config，并通过 PPTAgent 发起真实生成任务。</p>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <label>
          用户名
          <input v-model="form.username" autocomplete="username" />
        </label>
        <label>
          密码
          <input v-model="form.password" type="password" autocomplete="current-password" />
        </label>
        <p v-if="error" class="form-error">{{ error }}</p>
        <button class="btn primary" type="submit" :disabled="props.busy">
          <LogIn :size="18" />
          登录
        </button>
        <button class="btn ghost" type="button" :disabled="props.busy" @click="register">
          <UserPlus :size="18" />
          注册当前账号
        </button>
      </form>
    </section>
  </main>
</template>
