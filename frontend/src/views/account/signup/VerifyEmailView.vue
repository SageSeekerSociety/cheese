<!--
  What verifying the address shows (VerifyEmail.vue sends the code with the
  password and the agreements the sign-up flow still needs, then signs the new
  account in): the code box, the password box when a refresh dropped it, the
  agreement box when it has to be asked again, and the resend link.
-->
<template>
  <div>
    <AccountHeading :title="t('account.verifyEmail.title')" :lede="t('account.verifyEmail.sentTo', { email })" />

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form @submit.prevent="submitIfAllowed">
      <AccountField v-if="needsPassword" :label="t('account.field.password')" input-id="verify-password">
        <PasswordField
          id="verify-password"
          v-model="password"
          name="password"
          autocomplete="new-password"
          :hint="t('account.verifyEmail.passwordAgain')"
          persistent-hint
          v-bind="passwordProps"
        />
      </AccountField>

      <v-otp-input
        v-model="otp"
        length="6"
        type="number"
        v-bind="otpProps"
        class="account-otp"
        @update:model-value="handleOtpInput"
      />

      <LegalConsent
        v-if="needsConsent"
        ref="consentRef"
        :action-label="t('account.agreeAndSignUp')"
        :documents="consentDocuments"
        :load-error="consentLoadError"
        class="mb-4"
      />

      <BaseButton
        block
        kind="primary"
        size="lg"
        type="submit"
        class="account-submit"
        :loading="submitting"
        :disabled="otp?.length !== 6 || waiting"
      >
        {{ t('account.verifyEmail.submit') }}
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
        <NavLink to="/account/signin" class="account-link account-link--quiet">
          {{ t('account.backToSignIn') }}
        </NavLink>
      </div>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import type { AcceptedDocuments } from '@/network/api/legal/types'

import { computed, ref } from 'vue'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import LegalConsent from '@/components/account/LegalConsent.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** The address the code went to, named in the heading. */
  email: string
  /** The sentence for a verification that failed, or '' while there is none. */
  error: string
  /** A refresh dropped the password, so it is asked for again. */
  needsPassword: boolean
  /** The agreements did not survive the refresh, so they are asked for again. */
  needsConsent: boolean
  /** The server asked to wait: the form stays back. */
  waiting: boolean
  /** The container is waiting on the server. */
  submitting: boolean
  /** Seconds left before another code may be asked for; 0 when one may be. */
  resendWait: number
  /** The resend is on its way to the server. */
  resending: boolean
  /** The agreement versions to be agreed to, or null before they load. */
  consentDocuments: AcceptedDocuments | null
  /** The sentence for agreements that could not be loaded, or '' while there is none. */
  consentLoadError: string
}>()

const emit = defineEmits<{ submit: [value: { otp: string; password?: string }]; resend: [] }>()

const { handleSubmit, defineField } = useForm({
  validationSchema: computed(() =>
    toTypedSchema(
      z.object({
        otp: z
          .string()
          .length(6, { message: t('account.verifyEmail.codeInvalid') })
          .default(''),
        password: props.needsPassword ? z.string().min(1) : z.string().optional(),
      })
    )
  ),
})

const [otp, otpProps] = defineField('otp', vuetifyConfig)
const [password, passwordProps] = defineField('password', vuetifyConfig)

const consentRef = ref<InstanceType<typeof LegalConsent> | null>(null)

const submit = handleSubmit((value) => {
  emit('submit', value)
})

// The form is not sent while the server has asked to wait, nor while a send is
// already on its way: the same guard the request used to carry, kept before the
// validation so a held-back form stays quiet.
const submitIfAllowed = () => {
  if (props.submitting || props.waiting) return
  void submit()
}

const handleOtpInput = (value: string) => {
  if (value.length === 6 && (!props.needsPassword || password.value)) {
    submitIfAllowed()
  }
}

/** Asked by the container at submit time: the agreements this run will hand the server. */
const confirmConsent = () => consentRef.value?.confirm() ?? Promise.resolve(null)
defineExpose({ confirmConsent })
</script>
