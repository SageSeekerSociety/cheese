<!--
  In the browser, the start of a sign-in the desktop app asked for
  (lib/desktopApp.ts signInInBrowser): keeps the app's challenge for the page
  that hands the sign-in back (BackToApp.vue), then opens the sign-in, sign-up
  or reset page. Someone already signed in here goes straight to handing over.
  What it shows is AppSignInStartView.vue.
-->
<template>
  <AppSignInStartView />
</template>

<script lang="ts" setup>
import type { BrowserSignInEntry } from '@/lib/desktopApp'

import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ENTRY_PAGE, keepAppChallenge } from './appSignIn'
import AppSignInStartView from './AppSignInStartView.vue'

import { myId } from '@/me'

const route = useRoute()
const router = useRouter()

onMounted(() => {
  const challenge = route.query.challenge
  if (typeof challenge !== 'string' || !keepAppChallenge(challenge)) {
    router.replace({ name: 'SignIn' })
    return
  }
  if (myId()) {
    router.replace({ name: 'AppSignInHandOff' })
    return
  }
  const entry = route.query.entry
  const page =
    typeof entry === 'string' && Object.hasOwn(ENTRY_PAGE, entry) ? ENTRY_PAGE[entry as BrowserSignInEntry] : 'SignIn'
  router.replace({ name: page })
})
</script>
