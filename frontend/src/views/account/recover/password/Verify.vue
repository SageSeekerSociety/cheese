<!--
  Setting the new password a reset link carries: the token names the account,
  the form collects the new password twice, and the server trades the token for
  it. What it shows is VerifyView.vue.
-->
<template>
  <VerifyView :username="username" :error="error" :submitting="submitting" @submit="submit" />
</template>

<script lang="ts" setup>
import type { TokenPayload } from '@/network/api/users/types'
import type { SignInNoticeKey } from '../../signInNotice'

import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { jwtDecode } from 'jwt-decode'

import VerifyView from './VerifyView.vue'

import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'

const route = useRoute()
const token = computed(() => route.query.token as string)

// 从 token 中解析用户名
const username = computed(() => {
  try {
    const { payload } = jwtDecode<TokenPayload>(token.value)
    return payload.authorization.username
  } catch {
    return undefined
  }
})

const error = ref('')
const submitting = ref(false)

const router = useRouter()

const submit = async (password: string) => {
  error.value = ''
  if (!username.value) {
    error.value = t('account.resetPassword.invalidLink')
    return
  }
  submitting.value = true
  try {
    await UserApi.recoverPasswordVerify({
      token: token.value,
      password,
    })

    const message: SignInNoticeKey = 'passwordReset'
    router.replace({ name: 'SignIn', query: { username: username.value, message } })
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.resetPassword.failed'))
  } finally {
    submitting.value = false
  }
}
</script>
