<!--
  In the desktop app, where signing in, signing up and resetting a password
  start: all of them happen in the person's browser, where their password
  manager, passkeys and provider accounts are (appSignIn.ts). The sign-in comes
  back to this window through a `cheese://` link (AppSignInFinish.vue).
-->
<template>
  <div>
    <AccountHeading :title="t(`account.appSignIn.${entry}.title`)" :lede="t('account.appSignIn.lede')" />

    <v-alert v-if="errorMessage" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorMessage }}
    </v-alert>
    <v-alert v-else-if="url" type="info" variant="tonal" density="comfortable" class="mb-6">
      {{ t('account.appSignIn.waiting') }}
    </v-alert>

    <v-btn block color="primary" size="large" class="account-submit" :loading="busy" @click="open">
      <v-icon start icon="mdi-open-in-new" size="20" />
      {{ url ? t('account.appSignIn.openAgain') : t(`account.appSignIn.${entry}.open`) }}
    </v-btn>

    <v-btn v-if="url" block variant="text" class="mt-2" @click="copy">
      {{ t('account.appSignIn.copyLink') }}
    </v-btn>

    <p class="account-fine desktop-sign-in__switch">
      <template v-if="entry === 'signup'">
        {{ t('account.appSignIn.haveAccount') }}
        <a href="#" class="account-link" @click.prevent="switchTo('signin')">{{ t('account.appSignIn.toSignIn') }}</a>
      </template>
      <template v-else>
        {{ t('account.signIn.noAccount') }}
        <a href="#" class="account-link" @click.prevent="switchTo('signup')">{{ t('account.signIn.createAccount') }}</a>
      </template>
    </p>
  </div>
</template>

<script lang="ts" setup>
import type { BrowserSignInEntry } from '@/lib/desktopApp'

import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { ENTRY_PAGE } from './appSignIn'

import AccountHeading from '@/components/account/AccountHeading.vue'
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

<style scoped>
.desktop-sign-in__switch {
  margin-top: 24px;
  text-align: center;
}
</style>
