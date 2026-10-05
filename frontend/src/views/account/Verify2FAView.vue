<!--
  What the second-factor step shows (Verify2FA.vue): the trust-this-device
  box, the code box for the current code type, the back-to-sign-in link and
  the reminder shown after a backup code. Every value arrives as a prop; the
  edits, submit and the two dialog answers go out as events.
-->
<template>
  <div>
    <AccountHeading
      :title="t('account.twoFactor.title')"
      :lede="codeType === 'totp' ? t('account.twoFactor.totpLede') : t('account.twoFactor.backupLede')"
    />

    <v-alert v-if="errorMessage" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorMessage }}
    </v-alert>

    <v-form @submit.prevent="emit('submit')">
      <!-- Kept before the code box: the last digit auto-submits, so a checkbox
           placed after it could not be ticked in time. -->
      <v-checkbox
        :model-value="trustDevice"
        density="compact"
        hide-details
        class="verify-trust"
        @update:model-value="emit('update:trustDevice', $event)"
      >
        <template #label>
          <span class="verify-trust__label">{{ t('account.twoFactor.trustDevice') }}</span>
        </template>
      </v-checkbox>

      <v-otp-input
        v-if="codeType === 'totp'"
        :model-value="totpCode"
        length="6"
        type="number"
        class="account-otp"
        @update:model-value="emit('update:totpCode', $event)"
      />

      <v-otp-input
        v-else
        :model-value="backupCode"
        length="8"
        type="text"
        class="account-otp"
        @update:model-value="emit('update:backupCode', $event)"
      />

      <p class="account-hint">{{ t('account.twoFactor.lockout') }}</p>

      <BaseButton
        block
        kind="primary"
        size="lg"
        type="submit"
        class="account-submit"
        :loading="loading"
        :disabled="submitDisabled"
      >
        {{ codeType === 'totp' ? t('account.twoFactor.totpSubmit') : t('account.twoFactor.backupSubmit') }}
      </BaseButton>

      <div class="account-foot account-foot--split">
        <button type="button" class="account-link" @click="emit('toggle')">
          {{ codeType === 'totp' ? t('account.twoFactor.useBackup') : t('account.twoFactor.useTotp') }}
        </button>
        <router-link :to="backTo" class="account-link account-link--quiet">
          {{ t('account.backToSignIn') }}
        </router-link>
      </div>
    </v-form>

    <ConfirmDialog
      :model-value="showBackupCodeDialog"
      :title="t('account.twoFactor.backupUsedTitle')"
      :confirm-label="t('account.twoFactor.regenerate')"
      :cancel-label="t('account.twoFactor.later')"
      @update:model-value="emit('update:showBackupCodeDialog', $event)"
      @confirm="emit('confirm')"
      @cancel="emit('cancel')"
    >
      {{ t('account.twoFactor.backupUsedBody') }}
    </ConfirmDialog>
  </div>
</template>

<script setup lang="ts">
import type { NavTarget } from '@/lib/navTarget'

import AccountHeading from '@/components/account/AccountHeading.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'

defineProps<{
  codeType: 'totp' | 'backup'
  errorMessage: string
  loading: boolean
  submitDisabled: boolean
  trustDevice: boolean
  totpCode: string
  backupCode: string
  /** Where the back-to-sign-in link goes; the page works it out. */
  backTo: NavTarget
  showBackupCodeDialog: boolean
}>()

const emit = defineEmits<{
  submit: []
  toggle: []
  confirm: []
  cancel: []
  'update:trustDevice': [value: boolean]
  'update:totpCode': [value: string]
  'update:backupCode': [value: string]
  'update:showBackupCodeDialog': [value: boolean]
}>()
</script>

<style scoped>
.verify-trust {
  margin-bottom: 8px;
}

.verify-trust__label {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}
</style>
