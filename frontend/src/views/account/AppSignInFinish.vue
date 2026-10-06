<!--
  In the desktop app, the end of a sign-in made in the browser (BackToApp.vue):
  the code from the link and the secret this app kept (lib/desktopApp.ts) are
  traded for a sign-in of its own. What it shows is AppSignInFinishView.vue.
-->
<template>
  <AppSignInFinishView :failed="failed" />
</template>

<script lang="ts" setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AppSignInFinishView from './AppSignInFinishView.vue'

import { takeSignInVerifier } from '@/lib/desktopApp'
import { UserApi } from '@/network/api/users'
import { postLoginTarget } from '@/router/loginRedirect'
import AccountService from '@/services/account'

const route = useRoute()
const router = useRouter()

const failed = ref(false)

onMounted(async () => {
  const code = route.query.code
  // Only a sign-in this app started, and only once.
  const kept = takeSignInVerifier()
  if (typeof code !== 'string' || !kept) {
    failed.value = true
    return
  }
  try {
    await UserApi.finishAppSignIn(code, kept.verifier)
  } catch {
    failed.value = true
    return
  }
  if ((await AccountService.resumeFromCookie()) !== 'ok') {
    failed.value = true
    return
  }
  router.replace(postLoginTarget({ redirect: kept.target }))
})
</script>
