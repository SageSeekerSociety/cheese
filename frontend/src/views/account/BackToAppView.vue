<!--
  What the end of a browser sign-in shows (BackToApp.vue): the account it is
  for and the button that hands it over, the app link once it has one, or the
  failure if it could not be handed over.
-->
<template>
  <div>
    <template v-if="failed">
      <AccountHeading :title="t('account.oauth.error.title')" />
      <v-alert type="error" variant="tonal" density="comfortable" class="mb-6">
        {{ t('account.oauth.app.handOffFailed') }}
      </v-alert>
    </template>

    <template v-else-if="link">
      <AccountHeading :title="t('account.oauth.app.backTitle')" :lede="t('account.oauth.app.backLede')" />
      <BaseButton block kind="primary" size="lg" class="account-submit" :href="link">
        {{ t('account.oauth.app.open') }}
      </BaseButton>
    </template>

    <template v-else-if="asking">
      <AccountHeading
        :title="t('account.oauth.app.confirmTitle')"
        :lede="t('account.oauth.app.confirmLede', account)"
      />
      <BaseButton block kind="primary" size="lg" class="account-submit" :loading="handing" @click="emit('handOver')">
        {{ t('account.oauth.app.confirm') }}
      </BaseButton>
      <div class="back-to-app__other">
        <BaseButton kind="ghost" :disabled="handing" @click="emit('switchAccount')">
          {{ t('account.oauth.app.switchAccount') }}
        </BaseButton>
        <BaseButton kind="ghost" :disabled="handing" @click="emit('cancel')">
          {{ t('account.oauth.app.cancel') }}
        </BaseButton>
      </div>
    </template>

    <v-progress-linear v-else indeterminate color="primary" height="2" />
  </div>
</template>

<script lang="ts" setup>
import AccountHeading from '@/components/account/AccountHeading.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** The hand-off failed after the person continued. */
  failed: boolean
  /** The app link, once the sign-in has been handed over. */
  link: string
  /** The account is being named and the hand-over asked for. */
  asking: boolean
  /** The hand-over is on its way. */
  handing: boolean
  /** The account the sign-in would be handed over for. */
  account: { name: string; handle: string }
}>()

const emit = defineEmits<{
  handOver: []
  switchAccount: []
  cancel: []
}>()
</script>

<style scoped>
.back-to-app__other {
  display: flex;
  justify-content: center;
  gap: 8px;
  margin-top: 12px;
}
</style>
