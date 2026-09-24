<template>
  <transition name="account-page" mode="out-in">
    <EmailCodeStep
      v-if="step === 'code'"
      key="code"
      :email="email.trim()"
      :submit-label="t('account.addEmail.submit')"
      :verify="verify"
      :send="send"
      @change="step = 'email'"
    />

    <div v-else key="email">
      <AccountHeading :title="t('account.addEmail.title')" :lede="t('account.addEmail.lede')" />

      <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
        {{ error }}
      </v-alert>

      <v-form ref="formRef" @submit.prevent="submitEmail">
        <AccountField :label="t('account.field.email')" input-id="add-email">
          <v-text-field
            id="add-email"
            v-model="email"
            autocomplete="email"
            type="email"
            name="email"
            :rules="emailRules"
            :hint="t('account.rule.emailHint')"
            persistent-hint
          />
        </AccountField>

        <v-btn type="submit" block color="primary" size="large" class="account-submit" :loading="sending">
          {{ t('account.addEmail.send') }}
        </v-btn>

        <p class="account-foot">
          <button type="button" class="account-link account-link--quiet" :disabled="signingOut" @click="signOut">
            {{ t('account.addEmail.signOut') }}
          </button>
        </p>
      </v-form>
    </div>
  </transition>
</template>

<script setup lang="ts">
// Required of an account that has no address of its own: without one it
// cannot be recovered. The route guard (router/emailRequired.ts) brings every
// signed-in navigation here until the address is verified, then goes on to
// where the person was headed.
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { emailCodeMessage } from './attemptWait'
import EmailCodeStep from './EmailCodeStep.vue'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { postLoginTarget } from '@/router/loginRedirect'
import AccountService from '@/services/account'

const route = useRoute()
const router = useRouter()

const step = ref<'email' | 'code'>('email')
const email = ref('')
const error = ref('')
const sending = ref(false)
const signingOut = ref(false)
const formRef = ref()

const emailRules = [(v: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim()) || t('account.rule.emailInvalid')]

const goOn = () => router.replace(postLoginTarget(route.query))

const send = () => UserApi.sendAddEmailCode(email.value.trim())

async function submitEmail() {
  const { valid } = await formRef.value.validate()
  if (!valid) return
  error.value = ''
  sending.value = true
  try {
    await send()
    step.value = 'code'
  } catch (e) {
    error.value = emailCodeMessage(e) ?? t('account.verifyEmail.resendFailed')
  } finally {
    sending.value = false
  }
}

async function verify(code: string) {
  try {
    const { data } = await UserApi.addEmail({ email: email.value.trim(), code })
    AccountService.user = data.user
    localStorage.setItem('user', JSON.stringify(data.user))
    toast.success(t('account.addEmail.added'))
  } catch (e) {
    // Added meanwhile in another tab: the account needs nothing more.
    if ((e as { error?: { data?: { reason?: string } } })?.error?.data?.reason !== 'email_present') throw e
    await AccountService.updateUserInfo()
  }
  await goOn()
}

async function signOut() {
  signingOut.value = true
  // The same order as the account menu: the server drops the refresh token
  // first, or the next visit would restore the session.
  try {
    await UserApi.logout()
  } catch (e) {
    console.warn('Logout request failed; clearing local session anyway:', e)
  } finally {
    await AccountService.logout()
    router.replace({ name: 'SignIn' })
  }
}

onMounted(async () => {
  // Added meanwhile in another tab: this tab's copy of the account is older
  // than the server's, so ask it before showing the form.
  await AccountService.sessionRestored
  await AccountService.updateUserInfo()
  if (AccountService.user && !AccountService.user.emailMissing) goOn()
})
</script>
