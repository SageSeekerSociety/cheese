<!--
  What the desktop app shows where a sign-in, a sign-up or a password reset
  starts (DesktopSignIn.vue): one button that opens the person's browser, and,
  once it has, a way to open the same address again or copy it.
-->
<template>
  <div>
    <AccountHeading :title="t(`account.appSignIn.${entry}.title`)" :lede="t('account.appSignIn.lede')" />

    <v-alert v-if="errorMessage" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorMessage }}
    </v-alert>
    <v-alert v-else-if="opened" type="info" variant="tonal" density="comfortable" class="mb-6">
      {{ t('account.appSignIn.waiting') }}
    </v-alert>

    <v-btn block color="primary" size="large" class="account-submit" :loading="busy" @click="emit('open')">
      <v-icon start icon="mdi-open-in-new" size="20" />
      {{ opened ? t('account.appSignIn.openAgain') : t(`account.appSignIn.${entry}.open`) }}
    </v-btn>

    <v-btn v-if="opened" block variant="text" class="mt-2" @click="emit('copy')">
      {{ t('account.appSignIn.copyLink') }}
    </v-btn>

    <p class="account-fine desktop-sign-in__switch">
      <template v-if="entry === 'signup'">
        {{ t('account.appSignIn.haveAccount') }}
        <a href="#" class="account-link" @click.prevent="emit('switch', 'signin')">
          {{ t('account.appSignIn.toSignIn') }}
        </a>
      </template>
      <template v-else>
        {{ t('account.signIn.noAccount') }}
        <a href="#" class="account-link" @click.prevent="emit('switch', 'signup')">
          {{ t('account.signIn.createAccount') }}
        </a>
      </template>
    </p>
  </div>
</template>

<script lang="ts" setup>
import type { BrowserSignInEntry } from '@/lib/desktopApp'

import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'

defineProps<{
  entry: BrowserSignInEntry
  /** The browser has been opened on the sign-in at least once. */
  opened: boolean
  busy: boolean
  errorMessage: string
}>()

const emit = defineEmits<{
  open: []
  copy: []
  switch: [entry: BrowserSignInEntry]
}>()
</script>

<style scoped>
.desktop-sign-in__switch {
  margin-top: 24px;
  text-align: center;
}
</style>
