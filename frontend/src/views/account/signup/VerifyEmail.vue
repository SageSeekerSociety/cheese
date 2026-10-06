<!--
  Verifying the address a new account was registered under: the code goes to the
  server with the password and the agreements the sign-up flow still needs, a
  session comes back, and a refused attempt holds the form back for as long as
  the server asked. What it shows is VerifyEmailView.vue.
-->
<template>
  <VerifyEmailView
    ref="viewRef"
    :email="signupStore.email"
    :error="error"
    :needs-password="needsPassword"
    :needs-consent="needsConsent"
    :waiting="waiting"
    :submitting="submitting"
    :resend-wait="resendWait"
    :resending="resending"
    :consent-documents="consentDocuments"
    :consent-load-error="consentLoadError"
    @submit="submit"
    @resend="handleResend"
  />
</template>

<script lang="ts" setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { useConsentDocuments } from '@/composables/useConsentDocuments'

import { attemptMessage, useAttemptWait } from '../attemptWait'

import VerifyEmailView from './VerifyEmailView.vue'

import { t } from '@/i18n'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import AccountService from '@/services/account'
import { useSignupStore } from '@/stores/signup'

// The server refuses a new code within a minute of the last one.
const RESEND_COOLDOWN_SECONDS = 60

const router = useRouter()
const signupStore = useSignupStore()

// A refresh keeps the form (sessionStorage) but not the password, which is only
// ever held in memory; ask for it again rather than store it.
const needsPassword = !signupStore.password
// The consent chosen on the form is kept across a refresh; if it did not come
// back intact, it is asked for here instead of being assumed.
const needsConsent = !signupStore.consent

const viewRef = ref<InstanceType<typeof VerifyEmailView> | null>(null)
const { documents: consentDocuments, loadError: consentLoadError, load: loadConsentDocuments } = useConsentDocuments()
// 同意要交后端当前的协议版本；一进页面就取，提交时 `confirm()` 才有东西可交。
onMounted(() => {
  void loadConsentDocuments()
})

const error = ref('')
const { waiting, waitFor } = useAttemptWait()
const submitting = ref(false)

const submit = async (value: { otp: string; password?: string }) => {
  if (submitting.value || waiting.value) return
  error.value = ''
  if (needsConsent) {
    await loadConsentDocuments()
    const consent = await viewRef.value?.confirmConsent()
    if (!consent) return
    signupStore.consent = consent
  }
  if (needsPassword && value.password) signupStore.password = value.password
  submitting.value = true
  try {
    const { data } = await signupStore.signup(value.otp)
    // Registration answers with a session, the same as signing in: the person
    // has just chosen the password, so asking for it again would be busywork.
    await AccountService.login(data.accessToken, data.user)
    toast.success(t('account.verifyEmail.accountCreated'))
    router.replace('/')
  } catch (e) {
    error.value = attemptMessage(e) ?? requestErrorMessage(e, t('account.verifyEmail.failed'))
    waitFor(e)
  } finally {
    submitting.value = false
  }
}

const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined
const resendWait = computed(() =>
  Math.max(0, RESEND_COOLDOWN_SECONDS - Math.floor((now.value - signupStore.codeSentAt) / 1000))
)
const resending = ref(false)

const handleResend = async () => {
  error.value = ''
  resending.value = true
  try {
    await signupStore.resendCode()
    now.value = Date.now()
    toast.success(t('account.verifyEmail.resent'))
  } catch (e) {
    error.value = attemptMessage(e) ?? requestErrorMessage(e, t('account.verifyEmail.resendFailed'))
  } finally {
    resending.value = false
  }
}

onMounted(() => {
  // Opened directly, or after the session ended: there is nothing to verify.
  if (!signupStore.email) {
    router.replace({ name: 'SignUpStart' })
    return
  }
  ticker = setInterval(() => (now.value = Date.now()), 1000)
})

onBeforeUnmount(() => clearInterval(ticker))
</script>
