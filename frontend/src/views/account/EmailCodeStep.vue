<template>
  <div>
    <AccountHeading :title="t('account.verifyEmail.title')" :lede="t('account.verifyEmail.sentTo', { email })" />

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form @submit.prevent="submit">
      <v-otp-input
        v-model="code"
        length="6"
        type="number"
        class="account-otp"
        @update:model-value="(value: string) => value.length === 6 && submit()"
      />

      <v-btn
        block
        color="primary"
        size="large"
        type="submit"
        class="account-submit"
        :loading="submitting"
        :disabled="code.length !== 6 || waiting"
      >
        {{ submitLabel }}
      </v-btn>

      <div class="account-foot account-foot--split">
        <span>
          {{ t('account.verifyEmail.noCode') }}
          <span v-if="resendWait > 0" class="account-foot__wait">
            {{ t('account.verifyEmail.resendIn', { seconds: resendWait }) }}
          </span>
          <button v-else type="button" class="account-link" :disabled="resending" @click="resend">
            {{ t('account.verifyEmail.resend') }}
          </button>
        </span>
        <button type="button" class="account-link account-link--quiet" @click="emit('change')">
          {{ t('account.verifyEmail.changeEmail') }}
        </button>
      </div>
    </v-form>
  </div>
</template>

<script setup lang="ts">
// The code half of proving an address: the code sent to it, a resend once the
// server's minute has passed, and a way back to change the address. The screen
// that shows it sends the first code and says what a verified code leads to.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { emailCodeMessage, useAttemptWait } from './attemptWait'

import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'

// The server refuses a new code within a minute of the last one.
const RESEND_COOLDOWN_SECONDS = 60

const props = defineProps<{
  email: string
  submitLabel: string
  /** Checks the code; a rejection is shown here and the code cleared. */
  verify: (code: string) => Promise<void>
  /** Sends another code to the same address. */
  send: () => Promise<unknown>
}>()

const emit = defineEmits<{ change: [] }>()

const code = ref('')
const error = ref('')
const submitting = ref(false)
const resending = ref(false)
const { waiting, waitFor } = useAttemptWait()

const sentAt = ref(Date.now())
const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined
const resendWait = computed(() => Math.max(0, RESEND_COOLDOWN_SECONDS - Math.floor((now.value - sentAt.value) / 1000)))

async function submit() {
  if (submitting.value || waiting.value || code.value.length !== 6) return
  error.value = ''
  submitting.value = true
  try {
    await props.verify(code.value)
  } catch (e) {
    error.value = emailCodeMessage(e) ?? t('account.verifyEmail.failed')
    code.value = ''
    waitFor(e)
  } finally {
    submitting.value = false
  }
}

async function resend() {
  error.value = ''
  resending.value = true
  try {
    await props.send()
    sentAt.value = now.value = Date.now()
    toast.success(t('account.verifyEmail.resent'))
  } catch (e) {
    error.value = emailCodeMessage(e) ?? t('account.verifyEmail.resendFailed')
  } finally {
    resending.value = false
  }
}

onMounted(() => {
  ticker = setInterval(() => (now.value = Date.now()), 1000)
})
onBeforeUnmount(() => clearInterval(ticker))
</script>
