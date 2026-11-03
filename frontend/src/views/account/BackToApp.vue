<!--
  In the browser, the end of something the desktop app sent here: a sign-in
  (`signIn`), handed over as a code only the app can use, or an authorization
  whose result the app should show (backend/app/api/app_return.py). The app
  opens through a `cheese://` link; a browser may ask first, or open nothing
  without a click, so the button is always there.

  A sign-in is handed over only once the person says so, naming the account:
  any program on the computer can open this page with a challenge of its own,
  and someone already signed in here would otherwise hand it a sign-in unasked.
  A sign-in started on another origin of this deployment (appSignIn.ts
  pendingReturnOrigin) goes back there unasked: that code lands on a page of
  ours, which holds the only secret it is good with.
  What it shows is BackToAppView.vue.
-->
<template>
  <BackToAppView
    :failed="failed"
    :link="link"
    :asking="asking"
    :handing="handing"
    :account="account"
    @hand-over="handOver"
    @switch-account="switchAccount"
    @cancel="cancel"
  />
</template>

<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { forgetAppChallenge, pendingAppChallenge, pendingReturnOrigin } from './appSignIn'
import BackToAppView from './BackToAppView.vue'

import { UserAuthApi } from '@/api/userAuth'
import { appLink } from '@/lib/desktopApp'
import { myId } from '@/me'
import { postLoginTarget } from '@/router/loginRedirect'
import AccountService from '@/services/account'

const props = defineProps<{ signIn?: boolean }>()

const route = useRoute()
const router = useRouter()

const link = ref('')
const failed = ref(false)
const asking = ref(false)
const handing = ref(false)

const account = computed(() => {
  const user = AccountService.user
  return { name: user?.nickname || user?.username || '', handle: user?.username ?? '' }
})

function openApp(to: string) {
  link.value = to
  window.location.href = to
}

async function handOver() {
  const challenge = pendingAppChallenge()
  if (!challenge) {
    router.replace('/')
    return
  }
  handing.value = true
  try {
    const returnTo = pendingReturnOrigin()
    const data = returnTo
      ? await UserAuthApi.startAppSignIn(challenge, returnTo)
      : await UserAuthApi.startAppSignIn(challenge)
    forgetAppChallenge()
    const page = `/account/oauth/from-browser?${new URLSearchParams({ code: data.code })}`
    if (returnTo) {
      window.location.assign(`${returnTo}${page}`)
      return
    }
    openApp(appLink(page))
  } catch (e) {
    // Signed out here since this page opened: sign in again, and come back.
    if ((e as { response?: { status?: number } })?.response?.status === 401) {
      router.replace({ name: 'SignIn' })
      return
    }
    failed.value = true
  } finally {
    handing.value = false
  }
}

async function switchAccount() {
  await AccountService.logout()
  router.replace({ name: 'SignIn' })
}

function cancel() {
  forgetAppChallenge()
  router.replace('/')
}

onMounted(() => {
  if (!props.signIn) {
    openApp(appLink(postLoginTarget({ redirect: route.query.path })))
    return
  }
  // Not a sign-in the app asked for in this tab: an ordinary one, in this browser.
  if (!pendingAppChallenge()) {
    router.replace('/')
    return
  }
  if (!myId()) {
    router.replace({ name: 'SignIn' })
    return
  }
  // Back to the origin of this deployment the sign-in started on: the code
  // can only land on a page of ours, so there is nothing to ask.
  if (pendingReturnOrigin()) {
    void handOver()
    return
  }
  asking.value = true
})
</script>
