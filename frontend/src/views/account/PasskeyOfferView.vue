<!--
  What the offer of a passkey shows (PasskeyOffer.vue): the two buttons of
  equal size that accept it or hold it back, and, for an account that has been
  declined before, a quiet way to stop being asked.
-->
<template>
  <div v-if="offer">
    <AccountHeading :title="t('account.passkeyOffer.title')" :lede="t('account.passkeyOffer.lede')" />

    <v-alert v-if="errorMessage" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorMessage }}
    </v-alert>

    <!-- Declining is a button the same size as accepting, not a small link:
         the person is choosing, not being steered. -->
    <div class="account-actions">
      <BaseButton block kind="primary" size="lg" :loading="adding" :disabled="!!declining" @click="emit('add')">
        {{ t('account.passkeyOffer.add') }}
      </BaseButton>
      <BaseButton
        block
        kind="secondary"
        size="lg"
        :loading="declining === 'later'"
        :disabled="adding || declining === 'forever'"
        @click="emit('decline', false)"
      >
        {{ t('account.passkeyOffer.later') }}
      </BaseButton>
    </div>

    <div v-if="offer.canStopAsking" class="account-foot">
      <button
        type="button"
        class="account-link account-link--quiet"
        :disabled="adding || !!declining"
        @click="emit('decline', true)"
      >
        {{ t('account.passkeyOffer.stopAsking') }}
      </button>
    </div>
  </div>
</template>

<script lang="ts" setup>
import AccountHeading from '@/components/account/AccountHeading.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** The offer to draw; nothing is shown without one. */
  offer: { canStopAsking: boolean } | null
  adding: boolean
  declining: 'later' | 'forever' | null
  errorMessage: string
}>()

const emit = defineEmits<{
  add: []
  decline: [forever: boolean]
}>()
</script>
