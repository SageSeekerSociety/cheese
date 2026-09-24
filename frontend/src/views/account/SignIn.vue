<template>
  <div>
    <AccountHeading :title="t('account.signIn.title')">
      {{ t('account.signIn.noAccount') }}
      <router-link to="signup" class="account-link">{{ t('account.signIn.createAccount') }}</router-link>
    </AccountHeading>

    <v-alert v-if="errorMessage" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorMessage }}
    </v-alert>
    <v-alert v-else-if="notice" type="success" variant="tonal" density="comfortable" class="mb-6">
      {{ notice }}
    </v-alert>

    <!-- Whichever way this browser last used goes first; with no history, the
         one-click ways lead and the password form follows. Rendered in that
         order, not reordered by CSS, so the keyboard walks it the same way. -->
    <template v-for="part in parts" :key="part">
      <div v-if="part === 'alt'" class="signin-alt">
        <v-btn
          v-for="way in alternatives"
          :key="way.key"
          block
          variant="outlined"
          color="on-surface"
          size="large"
          class="signin-alt__btn"
          :loading="busy === way.key"
          :disabled="!!busy && busy !== way.key"
          @click="way.go"
        >
          <v-icon start :icon="way.icon" size="20" />
          {{ way.label }}
          <span v-if="way.key === last" class="signin-alt__last">{{ t('account.signIn.lastUsed') }}</span>
        </v-btn>
      </div>

      <div v-else-if="part === 'or'" class="signin-or">{{ t('account.signIn.or') }}</div>

      <v-form v-else @submit.prevent="login">
        <AccountField :label="t('account.field.username')" input-id="signin-username">
          <!-- `webauthn` lets the browser offer this device's passkeys right in
               the field's suggestions. -->
          <v-text-field
            id="signin-username"
            v-model="username"
            name="username"
            autocomplete="username webauthn"
            v-bind="usernameProps"
          />
        </AccountField>

        <AccountField :label="t('account.field.password')" input-id="signin-password">
          <template #aside>
            <router-link to="recover/password" class="account-link account-link--quiet">
              {{ t('account.signIn.forgotPassword') }}
            </router-link>
          </template>
          <PasswordField
            id="signin-password"
            v-model="password"
            name="password"
            autocomplete="current-password"
            v-bind="passwordProps"
          />
        </AccountField>

        <v-btn
          block
          color="primary"
          size="large"
          type="submit"
          class="account-submit"
          :loading="isSubmitting"
          :disabled="waiting"
        >
          {{ t('account.signIn.submit') }}
        </v-btn>
      </v-form>
    </template>

    <!-- 登录不建号（建号都在注册页和第三方首次建号页，那两处各有明确的
         同意），所以这里是告知，不是复选框（#1486）。放在所有登录方式
         下面，对哪一种都成立。 -->
    <p class="account-fine">
      {{ t('account.signInMeansYouAgreeTo') }}
      <LegalLinks />
    </p>
  </div>
</template>

<script lang="ts" setup>
import type { AuthenticationResponseJSON } from '@simplewebauthn/browser'
import type { OAuthProvider } from '@/network/api/users/types'
import type { User } from '@/types/users'
import type { SignInMethod } from './lastSignIn'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import {
  browserSupportsWebAuthn,
  browserSupportsWebAuthnAutofill,
  startAuthentication,
  WebAuthnAbortService,
} from '@simplewebauthn/browser'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import { attemptMessage, useAttemptWait } from './attemptWait'
import { lastSignIn, rememberSignIn } from './lastSignIn'
import { oauthProviderIcon } from './oauthProvider'
import { afterPasswordSignIn, passwordAccepted } from './passkeyEnrollment'
import { passkeyWrongHostMessage } from './passkeyHost'
import { signInNotice } from './signInNotice'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import LegalLinks from '@/components/account/LegalLinks.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { forgetOAuthRedirect, postLoginTarget, stashOAuthRedirect } from '@/router/loginRedirect'
import AccountService from '@/services/account'

const router = useRouter()
const route = useRoute()

// Signing in names an existing account, so only presence is checked here: the
// server is the one that knows whether the name and password are right.
const { handleSubmit, defineField, isSubmitting } = useForm({
  validationSchema: computed(() =>
    toTypedSchema(
      z.object({
        username: z.string().min(1),
        password: z.string().min(1),
      })
    )
  ),
})

const [username, usernameProps] = defineField('username', vuetifyConfig)
const [password, passwordProps] = defineField('password', vuetifyConfig)

const errorMessage = ref('')
const { waiting, waitFor } = useAttemptWait()
const notice = computed(() => signInNotice(route.query.message))
const webAuthnSupported = browserSupportsWebAuthn()
const last = lastSignIn()
/** The way in progress, so the others wait for it. */
const busy = ref<SignInMethod | null>(null)

if (route.query.username) {
  username.value = route.query.username as string
}

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

interface Way {
  key: SignInMethod
  label: string
  icon: string
  go: () => void
}

const alternatives = computed<Way[]>(() => {
  const ways: Way[] = oAuthProviders.value.map((p) => ({
    key: `oauth:${p.id}` as const,
    label: t('account.signIn.withProvider', { provider: p.name }),
    icon: oauthProviderIcon(p.id),
    go: () => handleOAuthLogin(p.id),
  }))
  if (webAuthnSupported) {
    ways.push({ key: 'passkey', label: t('account.signIn.passkey'), icon: 'mdi-key-chain', go: handlePasskeyLogin })
  }
  const i = ways.findIndex((w) => w.key === last)
  if (i > 0) ways.unshift(...ways.splice(i, 1))
  return ways
})

const parts = computed(() => {
  if (!alternatives.value.length) return ['form'] as const
  return last === 'password' ? (['form', 'or', 'alt'] as const) : (['alt', 'or', 'form'] as const)
})

function signedIn(method: SignInMethod, accessToken: string, user: User) {
  rememberSignIn(method)
  AccountService.login(accessToken, user)
  toast.success(t('account.signIn.signedIn'))
  router.replace(postLoginTarget(route.query))
}

const login = handleSubmit(async (value) => {
  if (waiting.value) return
  errorMessage.value = ''
  try {
    const { data } = await UserApi.login(value)
    if (data.requires2FA) {
      rememberSignIn('password')
      passwordAccepted()
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
    signedIn('password', data.accessToken!, data.user!)
    afterPasswordSignIn(data.user!.id, data.passkeyEnrollment)
  } catch (e) {
    errorMessage.value = attemptMessage(e) ?? requestErrorMessage(e, t('account.signIn.failed'))
    waitFor(e)
  }
})

async function finishPasskey(assertion: AuthenticationResponseJSON) {
  const { data } = await UserApi.verifyPasskeyAuthentication(assertion)
  signedIn('passkey', data.accessToken!, data.user!)
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

<style scoped>
.signin-alt {
  display: grid;
  gap: 10px;
}

.signin-alt__btn {
  position: relative;
  border-color: var(--line-2);
}

.signin-alt__last {
  position: absolute;
  top: 50%;
  right: 10px;
  padding: 1px 7px;
  font-size: 12px;
  font-weight: 500;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill-2);
  border-radius: var(--radius-sm);
  transform: translateY(-50%);
}

.signin-or {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 24px 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.signin-or::before,
.signin-or::after {
  flex: 1;
  height: 1px;
  content: '';
  background: var(--line);
}
</style>
