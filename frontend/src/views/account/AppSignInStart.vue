<!--
  In the browser, the start of a sign-in the desktop app asked for
  (lib/desktopApp.ts signInInBrowser): keeps the app's challenge for the page
  that hands the sign-in back (BackToApp.vue), then signs in with the provider
  as the sign-in page would.
-->
<template>
  <div>
    <AccountHeading :title="t('account.oauth.app.leaving', { provider: providerName })" />
    <v-progress-linear indeterminate color="primary" height="2" />
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { keepAppChallenge } from './appSignIn'
import { oauthProviderName } from './oauthProvider'

import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { stashOAuthRedirect } from '@/router/loginRedirect'

const route = useRoute()
const router = useRouter()

const provider = computed(() => (typeof route.query.provider === 'string' ? route.query.provider : ''))
const providerName = computed(() => oauthProviderName(provider.value))

onMounted(() => {
  const challenge = route.query.challenge
  if (!provider.value || typeof challenge !== 'string' || !keepAppChallenge(challenge)) {
    router.replace({ name: 'SignIn' })
    return
  }
  // Every way a provider sign-in can end — straight in, a new account, a
  // linked one, a second step — goes on to this page once signed in.
  stashOAuthRedirect('/account/oauth/to-app')
  UserApi.redirectToOAuthLogin(provider.value)
})
</script>
