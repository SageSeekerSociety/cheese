<template>
  <div>
    <AccountHeading
      :title="t('account.oauth.error.title')"
      :lede="
        providerName
          ? t('account.oauth.error.lede', { provider: providerName })
          : t('account.oauth.error.ledeUnknownProvider')
      "
    />

    <v-alert type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorDescription }}
    </v-alert>

    <v-btn
      block
      color="primary"
      size="large"
      to="/account/signin"
      style="text-transform: none; font-weight: 500; height: 48px"
      class="mb-4"
    >
      {{ t('account.backToSignIn') }}
    </v-btn>

    <v-btn
      v-if="providerId"
      block
      variant="outlined"
      color="on-surface"
      size="large"
      style="text-transform: none; font-weight: 500; height: 48px; border-color: var(--line-2)"
      @click="retryOAuth"
    >
      {{ t('account.oauth.error.retry', { provider: providerName }) }}
    </v-btn>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { oauthProviderName } from './oauthProvider'

import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'

const route = useRoute()

const queryText = (value: unknown) => (typeof value === 'string' ? value : '')

const providerId = computed(() => queryText(route.query.provider))
const providerName = computed(() => (providerId.value ? oauthProviderName(providerId.value) : ''))

// The server's own `error` text in the URL is English and anyone can rewrite
// it, so the page shows only what the error code maps to.
const errorDescription = computed(() => {
  switch (queryText(route.query.error_code)) {
    case 'ALREADY_LINKED':
      return t('account.oauth.error.alreadyLinked')
    case 'BINDING_FAILED':
      return t('account.oauth.error.bindingFailed')
    case 'CONSENT_REQUIRED':
      return t('account.oauth.error.consentRequired')
    case 'CREATION_FAILED':
      return t('account.oauth.error.creationFailed')
    case 'EMAIL_TAKEN':
      return t('account.oauth.error.emailTaken')
    case 'INVALID_CREDENTIALS':
      return t('account.oauth.error.invalidCredentials')
    case 'INVALID_INVITE_CODE':
      return t('account.oauth.error.invalidInviteCode')
    case 'INVALID_NICKNAME':
      return t('account.oauth.error.invalidNickname')
    case 'INVALID_PASSWORD':
      return t('account.oauth.error.invalidPassword')
    case 'INVALID_USERNAME':
      return t('account.rule.username')
    case 'INVITE_CODE_REQUIRED':
      return t('account.oauth.error.inviteCodeRequired')
    case 'SESSION_EXPIRED':
    case 'TOKEN_EXPIRED':
      return t('account.oauth.error.sessionExpired')
    case 'TOO_MANY_ATTEMPTS':
      return t('account.oauth.error.tooManyAttempts')
    case 'USERNAME_RESERVED':
      return t('account.oauth.error.usernameReserved')
    case 'USERNAME_TAKEN':
      return t('account.oauth.error.usernameTaken')
    case 'VERIFICATION_FAILED':
      return t('account.oauth.error.verificationFailed')
    case 'WEAK_PASSWORD':
      return t('account.rule.passwordInvalid')
    default:
      return t('account.oauth.error.unknown')
  }
})

const retryOAuth = () => {
  UserApi.redirectToOAuthLogin(providerId.value)
}
</script>
