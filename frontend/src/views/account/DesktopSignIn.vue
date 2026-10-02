<!--
  In the desktop app, where signing in, signing up and resetting a password
  start: all of them happen in the person's browser, where their password
  manager, passkeys and provider accounts are (appSignIn.ts). The sign-in comes
  back to this window through a `cheese://` link (AppSignInFinish.vue). What it
  shows is DesktopSignInView.vue.
-->
<template>
  <DesktopSignInView
    :entry="entry"
    :opened="!!url"
    :busy="busy"
    :error-message="errorMessage"
    @open="open"
    @copy="copy"
    @switch="switchTo"
  />
</template>

<script lang="ts" setup>
import type { BrowserSignInEntry } from '@/lib/desktopApp'

import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { ENTRY_PAGE } from './appSignIn'
import DesktopSignInView from './DesktopSignInView.vue'

import { t } from '@/i18n'
import { openInBrowser, signInInBrowser } from '@/lib/desktopApp'
import { postLoginTarget } from '@/router/loginRedirect'

const route = useRoute()
const router = useRouter()

const entry = computed<BrowserSignInEntry>(() => {
  const asked = route.query.entry
  return typeof asked === 'string' && Object.hasOwn(ENTRY_PAGE, asked) ? (asked as BrowserSignInEntry) : 'signin'
})

/** The address opened in the browser; opening it again reuses it, so the tab already open still works. */
const url = ref('')
const busy = ref(false)
const errorMessage = ref('')

async function open() {
  errorMessage.value = ''
  if (url.value) {
    openInBrowser(url.value)
    return
  }
  busy.value = true
  try {
    url.value = await signInInBrowser(entry.value, postLoginTarget(route.query))
  } catch {
    errorMessage.value = t('account.appSignIn.failed')
  } finally {
    busy.value = false
  }
}

async function copy() {
  try {
    await navigator.clipboard.writeText(url.value)
    toast.success(t('account.appSignIn.copied'))
  } catch {
    errorMessage.value = t('account.appSignIn.copyFailed')
  }
}

function switchTo(next: BrowserSignInEntry) {
  url.value = ''
  router.replace({ query: { ...route.query, entry: next } })
}
</script>
