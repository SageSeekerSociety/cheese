<!--
  What the browser shows when a third-party sign-in fails (OAuthError.vue reads
  the error code from the address and can send the person out to the provider
  again): the result in the title, the reason when the code adds one, and the
  way back to signing in or to the provider again.
-->
<template>
  <div>
    <!-- The title carries the result; the buttons carry the next step. The
         alert is only here when the error code adds a reason the title does
         not, so the failure is not stated twice. -->
    <AccountHeading :title="t('account.oauth.error.title')" />

    <v-alert v-if="errorDescription" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorDescription }}
    </v-alert>

    <div class="account-actions">
      <BaseButton block kind="primary" size="lg" to="/account/signin" class="account-submit">
        {{ t('account.backToSignIn') }}
      </BaseButton>
      <BaseButton v-if="providerId" block kind="secondary" size="lg" @click="emit('retry')">
        {{ t('account.oauth.error.retry', { provider: providerName }) }}
      </BaseButton>
    </div>
  </div>
</template>

<script lang="ts" setup>
import AccountHeading from '@/components/account/AccountHeading.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** The sentence the error code maps to, or '' when the title already says it. */
  errorDescription: string
  /** The provider's id from the address, or '' when there is none. */
  providerId: string
  /** The provider's display name, for the retry button. */
  providerName: string
}>()

const emit = defineEmits<{ retry: [] }>()
</script>
