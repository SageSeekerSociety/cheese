<template>
  <div>
    <!-- 标题区域 -->
    <div class="mb-12">
      <div class="d-flex align-center mb-3">
        <v-icon color="primary" size="28" class="mr-3">mdi-link-variant</v-icon>
        <h1 class="text-h3 font-weight-light" style="color: var(--ink); line-height: 1.2">绑定账户</h1>
      </div>
      <p class="text-body-1" style="color: var(--muted); line-height: 1.5">请输入密码以绑定您的账户</p>
    </div>

    <!-- 用户信息显示 -->
    <div class="mb-8">
      <v-alert type="info" variant="tonal" density="comfortable">
        <div class="font-weight-medium mb-1">绑定邮箱：</div>
        <div>{{ email }}</div>
      </v-alert>
    </div>

    <!-- 验证表单 -->
    <v-form ref="formRef" class="mb-8" @submit.prevent="handleVerify">
      <v-text-field
        id="field-password"
        v-model="password"
        autocomplete="current-password"
        name="password"
        label="密码"
        type="password"
        variant="outlined"
        density="comfortable"
        :rules="passwordRules"
        :error-messages="errorMessage"
        hide-details="auto"
        required
        class="mb-6"
      />

      <v-btn
        type="submit"
        block
        color="primary"
        size="large"
        :loading="loading"
        style="text-transform: none; font-weight: 500; height: 48px"
      >
        验证并绑定账户
      </v-btn>
    </v-form>

    <!-- 错误提示 -->
    <div v-if="error" class="mb-6">
      <v-alert type="error" variant="tonal">
        {{ error }}
      </v-alert>
    </div>

    <!-- 返回登录链接 -->
    <div class="text-center">
      <span style="color: var(--muted)">无法验证？</span>
      <router-link to="/account/signin" class="text-decoration-none" style="color: var(--accent-ink)">
        返回登录页面
      </router-link>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { UserApi } from '@/network/api/users'

const route = useRoute()
const router = useRouter()

const formRef = ref()
const loading = ref(false)
const error = ref('')
const errorMessage = ref('')
const password = ref('')

// URL 参数
const email = ref('')
const sessionId = ref('')
const stateToken = ref('') // 新的决策流程使用

// 验证规则
const passwordRules = [(v: string) => !!v || '请输入密码', (v: string) => v.length >= 8 || '密码至少8位']

// 处理验证
const handleVerify = async () => {
  const { valid } = await formRef.value.validate()
  if (!valid) return

  loading.value = true
  error.value = ''
  errorMessage.value = ''

  try {
    await verifyWithPassword()
  } catch (err) {
    handleVerifyError(err)
  } finally {
    loading.value = false
  }
}

// 密码验证
const verifyWithPassword = async () => {
  try {
    if (stateToken.value) {
      // 新的决策流程 - 使用 stateToken，直接提交表单
      UserApi.bindOAuthToUser({
        stateToken: stateToken.value,
        username: email.value,
        password: password.value,
      })
      // 后端会重定向，不需要处理响应
    } else {
      // 传统强制绑定流程 - 使用 sessionId
      await UserApi.verifyOAuth({
        sessionId: sessionId.value,
        password: password.value,
      })
      // 如果没有抛出异常，说明验证成功，等待后端重定向
    }
  } catch (err: any) {
    // 如果是重定向响应，直接跳转
    if (err.response && err.response.status === 302) {
      window.location.href = err.response.headers.location
      return
    }
    throw new Error('密码验证失败')
  }
}

// 处理验证错误
const handleVerifyError = (err: any) => {
  console.error('OAuth 验证失败:', err)

  if (err.message.includes('密码')) {
    errorMessage.value = '密码错误，请重新输入'
  } else {
    error.value = '验证失败，请重试'
  }
}

onMounted(() => {
  // 获取 URL 参数
  email.value = (route.query.email as string) || ''
  sessionId.value = (route.query.sessionId as string) || ''
  stateToken.value = (route.query.stateToken as string) || ''

  // 验证必要参数
  if (!email.value) {
    error.value = '验证参数不完整，请重新开始登录流程'
    return
  }

  // 必须有sessionId（传统流程）或stateToken（新决策流程）其中之一
  if (!sessionId.value && !stateToken.value) {
    error.value = '验证参数不完整，请重新开始登录流程'
    return
  }
})
</script>
