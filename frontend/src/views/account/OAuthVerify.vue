<template>
  <div>
    <AccountHeading :title="t('account.oauth.verify.title')" :lede="t('account.oauth.verify.lede', { email })" />

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form ref="formRef" @submit.prevent="handleVerify">
      <PasswordField
        id="field-password"
        v-model="password"
        autocomplete="current-password"
        name="password"
        :label="t('account.field.password')"
        :rules="passwordRules"
        :error-messages="errorMessage"
        required
        class="mb-2"
      />

      <v-btn
        type="submit"
        block
        color="primary"
        size="large"
        :loading="loading"
        style="text-transform: none; font-weight: 500; height: 48px"
        class="mb-4"
      >
        {{ t('account.oauth.verify.submit') }}
      </v-btn>

      <v-btn
        variant="text"
        color="primary"
        to="/account/signin"
        style="text-transform: none; padding: 0; min-width: auto"
        class="text-decoration-none"
      >
        {{ t('account.backToSignIn') }}
      </v-btn>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import AccountHeading from '@/components/account/AccountHeading.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'

const route = useRoute()

const formRef = ref()
const loading = ref(false)
const error = ref('')
const errorMessage = ref('')
const password = ref('')

// URL 参数
const email = ref('')
const sessionId = ref('')
const stateToken = ref('') // 新的决策流程使用

class WrongPassword extends Error {}

// 验证规则
const passwordRules = [(v: string) => !!v || t('account.oauth.verify.passwordRequired')]

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
    throw new WrongPassword()
  }
}

// 处理验证错误
const handleVerifyError = (err: any) => {
  console.error('OAuth 验证失败:', err)

  if (err instanceof WrongPassword) {
    errorMessage.value = t('account.oauth.verify.wrongPassword')
  } else {
    error.value = t('account.oauth.verify.failed')
  }
}

onMounted(() => {
  // 获取 URL 参数
  email.value = (route.query.email as string) || ''
  sessionId.value = (route.query.sessionId as string) || ''
  stateToken.value = (route.query.stateToken as string) || ''

  // 验证必要参数
  if (!email.value) {
    error.value = t('account.oauth.verify.incomplete')
    return
  }

  // 必须有sessionId（传统流程）或stateToken（新决策流程）其中之一
  if (!sessionId.value && !stateToken.value) {
    error.value = t('account.oauth.verify.incomplete')
    return
  }
})
</script>
