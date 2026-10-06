<!--
  What the desktop app shows while a sign-in made in the browser is traded for
  one of its own (AppSignInFinish.vue does the trading): a line that says it is
  working, or, when the link did not carry a sign-in this app started, the
  reason and the way back.
-->
<template>
  <div>
    <template v-if="failed">
      <AccountHeading :title="t('account.oauth.error.title')" />
      <v-alert type="error" variant="tonal" density="comfortable" class="mb-6">
        {{ t('account.oauth.app.expired') }}
      </v-alert>
      <BaseButton block kind="primary" size="lg" to="/account/signin" class="account-submit">
        {{ t('account.backToSignIn') }}
      </BaseButton>
    </template>

    <template v-else>
      <AccountHeading :title="t('account.oauth.success.processing')" />
      <v-progress-linear indeterminate color="primary" height="2" />
    </template>
  </div>
</template>

<script lang="ts" setup>
import AccountHeading from '@/components/account/AccountHeading.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** The link did not finish the sign-in: the app could not trade it for one. */
  failed: boolean
}>()
</script>
