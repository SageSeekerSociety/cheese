<!--
  Where the route guard (router/emailRequired.ts) brings an account with no
  address of its own until it verifies one, then on to where the person was
  headed. It reads the address, sends and checks the code, and signs out on
  request. What it shows is AddEmailView.vue.
-->
<template>
  <AddEmailView
    :step="step"
    :email="email"
    :error="error"
    :sending="sending"
    :signing-out="signingOut"
    :verify="verify"
    :send="send"
    @update:email="email = $event"
    @submit-email="submitEmail"
    @sign-out="signOut()"
    @change="step = 'email'"
  />
</template>

<script setup lang="ts">
// Required of an account that has no address of its own: without one it
// cannot be recovered. The route guard (router/emailRequired.ts) brings every
// signed-in navigation here until the address is verified, then goes on to
// where the person was headed.
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import AddEmailView from './AddEmailView.vue'
import { emailCodeMessage } from './attemptWait'

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

const goOn = () => router.replace(postLoginTarget(route.query))

const reasonOf = (e: unknown) => (e as { error?: { data?: { reason?: string } } })?.error?.data?.reason

// Only a recent sign-in may add the address. An older one is asked to sign in
// again and comes back here, on its way to the same place.
function whenSignInIsStale(e: unknown): never {
  if (reasonOf(e) === 'reauth_required') {
    toast.info(t('account.addEmail.signInAgain'))
    void signOut(postLoginTarget(route.query))
  }
  throw e
}

const send = () => UserApi.sendAddEmailCode(email.value.trim()).catch(whenSignInIsStale)

async function submitEmail() {
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
    if (reasonOf(e) !== 'email_present') whenSignInIsStale(e)
    await AccountService.updateUserInfo()
  }
  await goOn()
}

async function signOut(redirect?: string) {
  signingOut.value = true
  // The same order as the account menu: the server drops the refresh token
  // first, or the next visit would restore the session.
  try {
    await UserApi.logout()
  } catch (e) {
    console.warn('Logout request failed; clearing local session anyway:', e)
  } finally {
    await AccountService.logout()
    router.replace({ name: 'SignIn', query: redirect ? { redirect } : {} })
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
