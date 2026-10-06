<!--
  Signing in: the ways in (providers, a passkey, a mailed code) and the
  username/password form. It reads the address it was reached at, keeps the
  order the browser last used, and runs every way through the network. What it
  shows is SignInView.vue.
-->
<template>
  <SignInView
    :error-message="errorMessage"
    :notice="notice"
    :parts="parts"
    :alternatives="alternatives"
    :last="last"
    :busy="busy"
    :submitting="submitting"
    :waiting="waiting"
    :initial-username="initialUsername"
    @alt="chooseWay"
    @submit="login"
  />
</template>

<script lang="ts" setup>
import type { AuthenticationResponseJSON } from '@simplewebauthn/browser'
import type { OAuthProvider } from '@/network/api/users/types'
import type { User } from '@/types/users'
import type { SignInMethod } from './lastSignIn'
import type { SignInWay } from './SignInView.vue'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import {
  browserSupportsWebAuthn,
  browserSupportsWebAuthnAutofill,
  startAuthentication,
  WebAuthnAbortService,
} from '@simplewebauthn/browser'

import { attemptMessage, useAttemptWait } from './attemptWait'
import { lastSignIn, rememberSignIn } from './lastSignIn'
import { oauthProviderIcon } from './oauthProvider'
import { firstStepAccepted, landingAfterSignIn, takeFirstStep, upgradeAfterPasswordSignIn } from './passkeyEnrollment'
import { passkeyWrongHostMessage } from './passkeyHost'
import { signInNotice } from './signInNotice'
import SignInView from './SignInView.vue'

import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { forgetOAuthRedirect, postLoginTarget, stashOAuthRedirect } from '@/router/loginRedirect'
import AccountService from '@/services/account'

const router = useRouter()
const route = useRoute()

const errorMessage = ref('')
const submitting = ref(false)
const { waiting, waitFor } = useAttemptWait()
const notice = computed(() => signInNotice(route.query.message))
const webAuthnSupported = browserSupportsWebAuthn()
const last = lastSignIn()
/** The way in progress, so the others wait for it. */
const busy = ref<SignInMethod | null>(null)

const initialUsername = typeof route.query.username === 'string' ? route.query.username : ''

// The provider list rarely changes, so the last one seen is drawn straight
// away and the request only corrects it: the buttons above the form would
// otherwise arrive late and push the fields down under the cursor.
const PROVIDERS_KEY = 'cheese.oauthProviders'
function cachedProviders(): OAuthProvider[] {
  try {
    const list = JSON.parse(localStorage.getItem(PROVIDERS_KEY) ?? '[]')
    return Array.isArray(list) ? list : []
  } catch {
    return []
  }
}
const oAuthProviders = ref<OAuthProvider[]>(cachedProviders())

const alternatives = computed<SignInWay[]>(() => {
  const ways: SignInWay[] = oAuthProviders.value.map((p) => ({
    key: `oauth:${p.id}` as const,
    label: t('account.signIn.withProvider', { provider: p.name }),
    icon: oauthProviderIcon(p.id),
  }))
  if (webAuthnSupported) {
    ways.push({ key: 'passkey', label: t('account.signIn.passkey'), icon: 'mdi-key-chain' })
  }
  ways.push({ key: 'email_code', label: t('account.signIn.emailCode'), icon: 'mdi-email-outline' })
  const i = ways.findIndex((w) => w.key === last)
  if (i > 0) ways.unshift(...ways.splice(i, 1))
  return ways
})

const parts = computed(() => {
  if (!alternatives.value.length) return ['form'] as const
  return last === 'password' ? (['form', 'or', 'alt'] as const) : (['alt', 'or', 'form'] as const)
})

/** The view picked one of the ways in; each runs its own request. */
function chooseWay(key: SignInMethod) {
  if (key === 'passkey') {
    handlePasskeyLogin()
    return
  }
  if (key === 'email_code') {
    router.push({ name: 'SignInEmailCode', query: { redirect: route.query.redirect } })
    return
  }
  handleOAuthLogin(key.slice('oauth:'.length))
}

async function signedIn(
  method: SignInMethod,
  accessToken: string,
  user: User,
  passkeyEnrollment?: UserApi.PasskeyEnrollment
) {
  rememberSignIn(method)
  AccountService.login(accessToken, user)
  // After the login above: the upgrade's requests need the new session.
  const upgrade = method === 'password' ? upgradeAfterPasswordSignIn(user.id, passkeyEnrollment) : null
  toast.success(t('account.signIn.signedIn'))
  router.replace(await landingAfterSignIn(upgrade, postLoginTarget(route.query)))
}

async function login(value: { username: string; password: string }) {
  if (waiting.value) return
  errorMessage.value = ''
  submitting.value = true
  try {
    const { data } = await UserApi.login(value)
    if (data.requires2FA) {
      rememberSignIn('password')
      firstStepAccepted('password')
      router.push({
        name: 'Verify2FA',
        query: { token: data.tempToken, redirect: route.query.redirect },
      })
      return
    }
    // The waiting autofill ceremony is ended here, not when the page goes:
    // by then a passkey may be being created, and ending "the ceremony" would
    // end that one instead.
    stopAutofill()
    await signedIn('password', data.accessToken!, data.user!, data.passkeyEnrollment)
  } catch (e) {
    errorMessage.value = attemptMessage(e) ?? requestErrorMessage(e, t('account.signIn.failed'))
    waitFor(e)
  } finally {
    submitting.value = false
  }
}

async function finishPasskey(assertion: AuthenticationResponseJSON) {
  const { data } = await UserApi.verifyPasskeyAuthentication(assertion)
  await signedIn('passkey', data.accessToken!, data.user!)
}

function passkeyError(error: any, rpId?: string): string {
  // The browser's own error text is English and names WebAuthn internals, so
  // it is never shown; the cases a person can act on get their own sentence.
  const wrongHost = passkeyWrongHostMessage(error, rpId)
  if (wrongHost) return wrongHost
  if (error?.name === 'NotAllowedError') return t('account.signIn.passkeyCanceled')
  if (error?.response?.data?.code === 'PASSKEY_NOT_FOUND') return t('account.signIn.passkeyNotFound')
  return t('account.signIn.passkeyFailed')
}

const handlePasskeyLogin = async () => {
  errorMessage.value = ''
  busy.value = 'passkey'
  let rpId: string | undefined
  try {
    // Starting a ceremony cancels the waiting autofill one.
    const { data } = await UserApi.getPasskeyAuthenticationOptions()
    rpId = data.options.rpId
    await finishPasskey(await startAuthentication({ optionsJSON: data.options }))
  } catch (error) {
    errorMessage.value = passkeyError(error, rpId)
    startAutofill()
  } finally {
    busy.value = null
  }
}

// Offer this device's passkeys in the username field's suggestions. The
// ceremony waits quietly until one is picked; it ends without a word when the
// button above starts its own or the page is left.
let autofillOn = false
async function startAutofill() {
  if (autofillOn || !(await browserSupportsWebAuthnAutofill())) return
  autofillOn = true
  let assertion: AuthenticationResponseJSON
  try {
    const { data } = await UserApi.getPasskeyAuthenticationOptions()
    assertion = await startAuthentication({ optionsJSON: data.options, useBrowserAutofill: true })
  } catch {
    autofillOn = false
    return
  }
  autofillOn = false
  errorMessage.value = ''
  busy.value = 'passkey'
  try {
    await finishPasskey(assertion)
  } catch (error) {
    errorMessage.value = passkeyError(error)
    startAutofill()
  } finally {
    busy.value = null
  }
}

const fetchOAuthProviders = async () => {
  try {
    const { data } = await UserApi.getOAuthProviders()
    oAuthProviders.value = data.providers
    try {
      localStorage.setItem(PROVIDERS_KEY, JSON.stringify(data.providers))
    } catch {
      // storage unavailable — the next visit asks again
    }
  } catch (error) {
    console.error('获取 OAuth 提供商失败:', error)
  }
}

const handleOAuthLogin = (providerId: string) => {
  errorMessage.value = ''
  busy.value = `oauth:${providerId}`
  try {
    // 生成随机 state 参数用于防止 CSRF 攻击，存下来供回来时核对
    const state = crypto.randomUUID()
    localStorage.setItem('oauth_state', state)
    stashOAuthRedirect(postLoginTarget(route.query))
    UserApi.redirectToOAuthLogin(providerId, state)
  } catch {
    busy.value = null
    errorMessage.value = t('account.signIn.providerFailed')
  }
}

onMounted(() => {
  forgetOAuthRedirect()
  // Likewise a password accepted on an earlier visit: a second step reached
  // from here next time follows whichever way this visit signs in.
  takeFirstStep()
  fetchOAuthProviders()
  startAutofill()
})

function stopAutofill() {
  if (!autofillOn) return
  autofillOn = false
  WebAuthnAbortService.cancelCeremony()
}

onBeforeUnmount(stopAutofill)
</script>
