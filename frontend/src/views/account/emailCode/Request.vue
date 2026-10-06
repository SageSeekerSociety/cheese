<!--
  Asking for a sign-in code by mail: the address is sent to the server, the
  code page remembers it for a refresh, and a refused attempt holds the form
  back for as long as the server asked. What it shows is RequestView.vue.
-->
<template>
  <RequestView
    :initial-email="initialEmail"
    :error="error"
    :waiting="waiting"
    :submitting="submitting"
    :back-to-sign-in="backToSignIn"
    @submit="submit"
  />
</template>

<script lang="ts" setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { attemptMessage, useAttemptWait } from '../attemptWait'

import { pendingCode, rememberCodeSent } from './pendingCode'
import RequestView from './RequestView.vue'

import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'

const router = useRouter()
const route = useRoute()

const initialEmail = pendingCode()?.email ?? ''

// The way back keeps where the sign-in was headed.
const backToSignIn = computed(() => ({ name: 'SignIn', query: { redirect: route.query.redirect } }))

const error = ref('')
const submitting = ref(false)
const { waiting, waitFor } = useAttemptWait()

const submit = async (email: string) => {
  if (waiting.value) return
  error.value = ''
  const address = email.trim()
  submitting.value = true
  try {
    await UserApi.requestSignInCode(address)
    rememberCodeSent(address)
    router.push({ name: 'SignInEmailCodeVerify', query: { redirect: route.query.redirect } })
  } catch (e) {
    error.value = attemptMessage(e) ?? requestErrorMessage(e, t('account.verifyEmail.resendFailed'))
    waitFor(e)
  } finally {
    submitting.value = false
  }
}
</script>
