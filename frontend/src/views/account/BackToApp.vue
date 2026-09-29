<!--
  In the browser, the end of something the desktop app sent here: a sign-in
  (`signIn`), handed over as a code only the app can use, or an authorization
  whose result the app should show (backend/app/api/app_return.py). The app
  opens through a `cheese://` link; a browser may ask first, or open nothing
  without a click, so the button is always there.
-->
<template>
  <div>
    <template v-if="failed">
      <AccountHeading :title="t('account.oauth.error.title')" />
      <v-alert type="error" variant="tonal" density="comfortable" class="mb-6">
        {{ t('account.oauth.app.handOffFailed') }}
      </v-alert>
    </template>

    <template v-else-if="link">
      <AccountHeading :title="t('account.oauth.app.backTitle')" :lede="t('account.oauth.app.backLede')" />
      <v-btn block color="primary" size="large" class="account-submit" :href="link">
        {{ t('account.oauth.app.open') }}
      </v-btn>
    </template>

    <v-progress-linear v-else indeterminate color="primary" height="2" />
  </div>
</template>

<script lang="ts" setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { takeAppChallenge } from './appSignIn'

import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'
import { appLink } from '@/lib/desktopApp'
import { UserApi } from '@/network/api/users'
import { postLoginTarget } from '@/router/loginRedirect'

const props = defineProps<{ signIn?: boolean }>()

const route = useRoute()
const router = useRouter()

const link = ref('')
const failed = ref(false)

async function signInLink(): Promise<string | null> {
  const challenge = takeAppChallenge()
  // Not a sign-in the app asked for in this tab: an ordinary one, in this browser.
  if (!challenge) return null
  const { data } = await UserApi.startAppSignIn(challenge)
  return appLink(`/account/oauth/from-browser?${new URLSearchParams({ code: data.code })}`)
}

onMounted(async () => {
  try {
    link.value = (props.signIn ? await signInLink() : appLink(postLoginTarget({ redirect: route.query.path }))) ?? ''
  } catch {
    failed.value = true
    return
  }
  if (!link.value) {
    router.replace('/')
    return
  }
  window.location.href = link.value
})
</script>
