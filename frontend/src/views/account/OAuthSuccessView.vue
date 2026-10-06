<!--
  What the browser shows while a third-party sign-in coming back is finished
  (OAuthSuccess.vue trades the redirect's refresh cookie for a session): a line
  that says it is working, or, when it did not finish, why and the way back.
-->
<template>
  <div>
    <template v-if="error">
      <AccountHeading :title="t('account.oauth.error.title')" />
      <v-alert type="error" variant="tonal" density="comfortable" class="mb-6">
        {{ error }}
      </v-alert>
      <BaseButton block kind="primary" size="lg" to="/account/signin" class="account-submit">
        {{ t('account.backToSignIn') }}
      </BaseButton>
    </template>

    <template v-else>
      <AccountHeading
        :title="processing ? t('account.oauth.success.processing') : t('account.oauth.success.done')"
        :lede="
          processing
            ? t('account.oauth.success.processingLede', { provider: providerName })
            : t('account.oauth.success.doneLede')
        "
      />
      <v-progress-linear indeterminate color="primary" height="2" />
    </template>
  </div>
</template>

<script lang="ts" setup>
import AccountHeading from '@/components/account/AccountHeading.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** The session is still being read from the redirect's cookie. */
  processing: boolean
  /** The sentence for a sign-in that did not finish, or '' while it is fine. */
  error: string
  /** The provider's display name, for the "signing in with …" line. */
  providerName: string
}>()
</script>
