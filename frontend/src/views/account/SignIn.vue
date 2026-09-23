<template>
  <div>
    <AccountHeading :title="t('account.signIn.title')" />

    <v-alert v-if="errorMessage" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorMessage }}
    </v-alert>
    <v-alert v-else-if="notice" type="success" variant="tonal" density="comfortable" class="mb-6">
      {{ notice }}
    </v-alert>

    <v-form ref="loginForm" @submit.prevent="login">
      <v-text-field
        id="signin-username"
        v-model="username"
        name="username"
        autocomplete="username"
        :label="t('account.field.username')"
        v-bind="usernameProps"
      />

      <PasswordField
        id="signin-password"
        v-model="password"
        name="password"
        autocomplete="current-password"
        :label="t('account.field.password')"
        v-bind="passwordProps"
      />

      <div class="d-flex justify-end mt-n2 mb-4">
        <v-btn
          variant="text"
          color="primary"
          to="recover/password"
          style="text-transform: none; padding: 0; min-width: auto"
        >
          {{ t('account.signIn.forgotPassword') }}
        </v-btn>
      </div>

      <v-btn
        block
        color="primary"
        size="large"
        type="submit"
        :loading="isSubmitting"
        style="text-transform: none; font-weight: 500; height: 48px"
        class="mb-4"
      >
        {{ t('account.signIn.submit') }}
      </v-btn>

      <p class="text-body-2" style="color: var(--muted)">
        {{ t('account.signIn.noAccount') }}
        <v-btn
          variant="text"
          color="primary"
          to="signup"
          style="text-transform: none; padding: 0; min-width: auto; height: auto; vertical-align: baseline"
          class="text-decoration-none"
          >{{ t('account.signIn.createAccount') }}</v-btn
        >
      </p>
    </v-form>

    <div class="d-flex align-center my-6">
      <v-divider class="flex-grow-1" />
      <span class="px-4 text-body-2" style="color: var(--faint)">{{ t('account.signIn.or') }}</span>
      <v-divider class="flex-grow-1" />
    </div>

    <div class="d-flex flex-column" style="gap: 12px">
      <v-btn
        block
        variant="outlined"
        color="on-surface"
        size="large"
        :loading="isPasskeyLoading"
        :disabled="!webAuthnSupported"
        class="alt-method"
        @click="handlePasskeyLogin"
      >
        <v-icon start icon="mdi-key-chain" size="20" /> {{ t('account.signIn.passkey') }}
      </v-btn>
      <p v-if="!webAuthnSupported" class="text-body-2" style="color: var(--faint)">
        {{ t('account.signIn.passkeyUnsupported') }}
      </p>

      <v-btn
        v-for="provider in oAuthProviders"
        :key="provider.id"
        block
        variant="outlined"
        color="on-surface"
        size="large"
        :loading="oAuthLoading === provider.id"
        class="alt-method"
        @click="handleOAuthLogin(provider.id)"
      >
        <v-icon start :icon="getProviderIcon(provider.id)" size="20" />
        {{ t('account.signIn.withProvider', { provider: provider.name }) }}
      </v-btn>
    </div>

    <!-- 登录不建号（建号都在注册页和第三方首次建号页，那两处各有明确的
         同意），所以这里是告知，不是复选框（#1486）。放在所有登录方式
         下面，对哪一种都成立。 -->
    <p class="text-body-2 mt-8" style="color: var(--muted)">
      {{ t('account.signInMeansYouAgreeTo') }}
      <LegalLinks />
    </p>
  </div>
</template>

<script lang="ts" setup>
import type { OAuthProvider } from '@/network/api/users/types'

import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { startAuthentication } from '@simplewebauthn/browser'
import { browserSupportsWebAuthn } from '@simplewebauthn/browser'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import { signInNotice } from './signInNotice'

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
const notice = computed(() => signInNotice(route.query.message))
const isPasskeyLoading = ref(false)
const webAuthnSupported = ref(browserSupportsWebAuthn())
const oAuthProviders = ref<OAuthProvider[]>([])
const oAuthLoading = ref<string | null>(null)

// 如果 URL 中有 username 参数，自动填充用户名
if (route.query.username) {
  username.value = route.query.username as string
}

const login = handleSubmit(async (value) => {
  errorMessage.value = ''
  try {
    const { data } = await UserApi.login(value)
    if (data.requires2FA) {
      router.push({
        name: 'Verify2FA',
        query: { token: data.tempToken, redirect: route.query.redirect },
      })
      return
    }
    AccountService.login(data.accessToken!, data.user!)
    toast.success(t('account.signIn.signedIn'))
    router.replace(postLoginTarget(route.query))
  } catch (e) {
    errorMessage.value = requestErrorMessage(e, t('account.signIn.failed'))
  }
})

// 处理通行密钥登录
const handlePasskeyLogin = async () => {
  errorMessage.value = ''
  isPasskeyLoading.value = true
  try {
    const optionsResponse = await UserApi.getPasskeyAuthenticationOptions()
    const optionsJSON = optionsResponse.data.options
    const asseResp = await startAuthentication({ optionsJSON })
    const { data } = await UserApi.verifyPasskeyAuthentication(asseResp)

    AccountService.login(data.accessToken!, data.user!)
    toast.success(t('account.signIn.signedIn'))
    router.replace(postLoginTarget(route.query))
  } catch (error: any) {
    // The browser's own error text is English and names WebAuthn internals, so
    // it is never shown; the cases a person can act on get their own sentence.
    if (error?.name === 'NotAllowedError') {
      errorMessage.value = t('account.signIn.passkeyCanceled')
    } else if (error?.response?.data?.code === 'PASSKEY_NOT_FOUND') {
      errorMessage.value = t('account.signIn.passkeyNotFound')
    } else {
      errorMessage.value = t('account.signIn.passkeyFailed')
    }
  } finally {
    isPasskeyLoading.value = false
  }
}

// 获取 OAuth 提供商
const fetchOAuthProviders = async () => {
  try {
    const response = await UserApi.getOAuthProviders()
    oAuthProviders.value = response.data.providers
  } catch (error) {
    console.error('获取 OAuth 提供商失败:', error)
  }
}

// 处理 OAuth 登录
const handleOAuthLogin = async (providerId: string) => {
  errorMessage.value = ''
  oAuthLoading.value = providerId
  try {
    // 生成随机 state 参数用于防止 CSRF 攻击
    const state = crypto.randomUUID()

    // 将 state 存储到 localStorage，用于后续验证
    localStorage.setItem('oauth_state', state)
    stashOAuthRedirect(postLoginTarget(route.query))

    // 跳转到 OAuth 登录页面
    UserApi.redirectToOAuthLogin(providerId, state)
  } catch {
    oAuthLoading.value = null
    errorMessage.value = t('account.signIn.providerFailed')
  }
}

// 获取提供商图标
const getProviderIcon = (providerId: string) => {
  const iconMap: Record<string, string> = {
    github: 'mdi-github',
    google: 'mdi-google',
    microsoft: 'mdi-microsoft',
    qq: 'mdi-qqchat',
    wechat: 'mdi-wechat',
    weibo: 'mdi-sina-weibo',
  }
  return iconMap[providerId] || 'mdi-account-circle'
}

onMounted(() => {
  forgetOAuthRedirect()
  fetchOAuthProviders()
})
</script>

<style scoped>
.alt-method {
  height: 48px;
  font-weight: 500;
  text-transform: none;
  border-color: var(--line-2);
}
</style>
