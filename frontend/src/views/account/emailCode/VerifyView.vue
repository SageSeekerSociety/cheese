<!--
  What the mailed-code sign-in screen shows (emailCode/Verify.vue): the address
  the code went to, the six boxes, and the footer with the resend countdown and
  the way back to sign in.
-->
<template>
  <div v-if="pending">
    <AccountHeading
      :title="t('account.emailCode.codeTitle')"
      :lede="t('account.verifyEmail.sentTo', { email: pending.email })"
    />

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form @submit.prevent="submit">
      <v-otp-input
        :model-value="code"
        length="6"
        type="number"
        class="account-otp"
        :aria-label="t('account.emailCode.codeTitle')"
        :disabled="submitting"
        @update:model-value="(value: string) => emit('update:code', value)"
        @finish="submit"
      />

      <BaseButton
        block
        kind="primary"
        size="lg"
        type="submit"
        class="account-submit"
        :loading="submitting"
        :disabled="code.length !== 6 || waiting"
      >
        {{ t('account.signIn.submit') }}
      </BaseButton>

      <div class="account-foot account-foot--split">
        <span>
          {{ t('account.verifyEmail.noCode') }}
          <span v-if="resendWait > 0" class="account-foot__wait">
            {{ t('account.verifyEmail.resendIn', { seconds: resendWait }) }}
          </span>
          <button v-else type="button" class="account-link" :disabled="resending" @click="emit('resend')">
            {{ t('account.verifyEmail.resend') }}
          </button>
        </span>
        <router-link :to="backToSignIn" class="account-link account-link--quiet">
          {{ t('account.backToSignIn') }}
        </router-link>
      </div>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import AccountHeading from '@/components/account/AccountHeading.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** The address a code was sent to; nothing is shown without one. */
  pending: { email: string } | null
  /** The digits entered so far. */
  code: string
  error: string
  submitting: boolean
  /** The server asked to wait before the next attempt. */
  waiting: boolean
  resending: boolean
  /** Seconds before another code may be asked for; 0 for none. */
  resendWait: number
  /** Where the "back to sign in" link goes, already resolved. */
  backToSignIn: string
}>()

const emit = defineEmits<{
  'update:code': [code: string]
  submit: []
  resend: []
}>()

const submit = () => emit('submit')
</script>
