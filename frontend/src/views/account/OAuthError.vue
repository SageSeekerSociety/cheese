<!--
  Where a third-party sign-in that failed comes back to: the error code in the
  address is turned into one sentence, and the retry button goes out to the
  provider again. What it shows is OAuthErrorView.vue.
-->
<template>
  <OAuthErrorView
    :error-description="errorDescription"
    :provider-id="providerId"
    :provider-name="providerName"
    @retry="retryOAuth"
  />
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import OAuthErrorView from './OAuthErrorView.vue'
import { oauthProviderName } from './oauthProvider'

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
    case 'EMAIL_UNVERIFIED':
      return t('account.oauth.error.emailUnverified')
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
      // No reason to add: the title already says the sign-in failed, and the
      // buttons below are the next step.
      return ''
  }
})

const retryOAuth = () => {
  UserApi.redirectToOAuthLogin(providerId.value)
}
</script>
