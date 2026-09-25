<template>
  <div v-if="pending">
    <AccountHeading
      :title="t('account.emailCode.codeTitle')"
      :lede="t('account.verifyEmail.sentTo', { email: pending.email })"
    />

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form @submit.prevent="submit">
      <v-otp-input
        v-model="code"
        length="6"
        type="number"
        class="account-otp"
        :aria-label="t('account.emailCode.codeTitle')"
        :disabled="submitting"
        @finish="submit"
      />

      <v-btn
        block
        color="primary"
        size="large"
        type="submit"
        class="account-submit"
        :loading="submitting"
        :disabled="code.length !== 6 || waiting"
      >
        {{ t('account.signIn.submit') }}
      </v-btn>

      <div class="account-foot account-foot--split">
        <span>
          {{ t('account.verifyEmail.noCode') }}
          <span v-if="resendWait > 0" class="account-foot__wait">
            {{ t('account.verifyEmail.resendIn', { seconds: resendWait }) }}
          </span>
          <button v-else type="button" class="account-link" :disabled="resending" @click="resend">
            {{ t('account.verifyEmail.resend') }}
          </button>
        </span>
        <router-link :to="backToSignIn" class="account-link account-link--quiet">
          {{ t('account.backToSignIn') }}
        </router-link>
      </div>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { attemptMessage, useAttemptWait } from '../attemptWait'
import { rememberSignIn } from '../lastSignIn'
import { firstStepAccepted, landingAfterSignIn, upgradeAfterEmailCodeSignIn } from '../passkeyEnrollment'

import { forgetPendingCode, pendingCode, rememberCodeSent } from './pendingCode'

import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { postLoginTarget } from '@/router/loginRedirect'
import AccountService from '@/services/account'

// The server refuses a new code within a minute of the last one.
const RESEND_COOLDOWN_SECONDS = 60

const router = useRouter()
const route = useRoute()

const backToSignIn = computed(() => ({ name: 'SignIn', query: { redirect: route.query.redirect } }))

const pending = ref(pendingCode())
const code = ref('')
const error = ref('')
const submitting = ref(false)
const { waiting, waitFor } = useAttemptWait()

async function submit() {
  if (!pending.value || submitting.value || waiting.value || code.value.length !== 6) return
  error.value = ''
  submitting.value = true
  try {
    const { data } = await UserApi.signInWithEmailCode({ email: pending.value.email, code: code.value })
    rememberSignIn('email_code')
    forgetPendingCode()
    if (data.requires2FA) {
      firstStepAccepted('email_code')
      router.push({ name: 'Verify2FA', query: { token: data.tempToken, redirect: route.query.redirect } })
      return
    }
    AccountService.login(data.accessToken!, data.user!)
    toast.success(t('account.signIn.signedIn'))
    const upgrade = upgradeAfterEmailCodeSignIn(data.user!.id, data.passkeyEnrollment)
    router.replace(await landingAfterSignIn(upgrade, postLoginTarget(route.query)))
  } catch (e) {
    error.value = attemptMessage(e) ?? requestErrorMessage(e, t('account.verifyEmail.failed'))
    code.value = ''
    waitFor(e)
  } finally {
    submitting.value = false
  }
}

const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined
const resendWait = computed(() =>
  Math.max(0, RESEND_COOLDOWN_SECONDS - Math.floor((now.value - (pending.value?.sentAt ?? 0)) / 1000))
)
const resending = ref(false)

async function resend() {
  if (!pending.value) return
  error.value = ''
  resending.value = true
  try {
    await UserApi.requestSignInCode(pending.value.email)
    rememberCodeSent(pending.value.email)
    pending.value = pendingCode()
    now.value = Date.now()
    toast.success(t('account.verifyEmail.resent'))
  } catch (e) {
    error.value = attemptMessage(e) ?? requestErrorMessage(e, t('account.verifyEmail.resendFailed'))
  } finally {
    resending.value = false
  }
}

onMounted(() => {
  // Opened directly, or in a tab that never asked for a code.
  if (!pending.value) {
    router.replace({ name: 'SignInEmailCode', query: { redirect: route.query.redirect } })
    return
  }
  ticker = setInterval(() => (now.value = Date.now()), 1000)
})

onBeforeUnmount(() => clearInterval(ticker))
</script>
