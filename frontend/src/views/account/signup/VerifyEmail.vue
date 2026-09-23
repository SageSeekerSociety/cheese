<template>
  <div>
    <AccountHeading
      :title="t('account.verifyEmail.title')"
      :lede="t('account.verifyEmail.sentTo', { email: signupStore.email })"
    />

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form @submit.prevent="submit">
      <PasswordField
        v-if="needsPassword"
        id="verify-password"
        v-model="password"
        name="password"
        autocomplete="new-password"
        :label="t('account.field.password')"
        :hint="t('account.verifyEmail.passwordAgain')"
        persistent-hint
        v-bind="passwordProps"
        class="mb-2"
      />

      <v-otp-input
        v-model="otp"
        length="6"
        type="number"
        variant="outlined"
        v-bind="otpProps"
        class="mb-6"
        @update:model-value="handleOtpInput"
      />

      <LegalConsent v-if="needsConsent" ref="consentRef" :action-label="t('account.agreeAndSignUp')" class="mb-4" />

      <v-btn
        block
        color="primary"
        size="large"
        type="submit"
        :loading="submitting"
        :disabled="otp?.length !== 6"
        style="text-transform: none; font-weight: 500; height: 48px"
        class="mb-4"
      >
        {{ t('account.verifyEmail.submit') }}
      </v-btn>

      <div class="d-flex align-center justify-space-between flex-wrap" style="gap: 8px">
        <p class="text-body-2" style="color: var(--muted)">
          {{ t('account.verifyEmail.noCode') }}
          <span v-if="resendWait > 0" style="color: var(--faint)">
            {{ t('account.verifyEmail.resendIn', { seconds: resendWait }) }}
          </span>
          <v-btn
            v-else
            variant="text"
            color="primary"
            :loading="resending"
            style="text-transform: none; padding: 0; min-width: auto; height: auto; vertical-align: baseline"
            class="text-decoration-none"
            @click="handleResend"
          >
            {{ t('account.verifyEmail.resend') }}
          </v-btn>
        </p>
        <v-btn
          variant="text"
          color="primary"
          to="/account/signin"
          style="text-transform: none; padding: 0; min-width: auto"
        >
          {{ t('account.backToSignIn') }}
        </v-btn>
      </div>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import AccountHeading from '@/components/account/AccountHeading.vue'
import LegalConsent from '@/components/account/LegalConsent.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import { t } from '@/i18n'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import AccountService from '@/services/account'
import { useSignupStore } from '@/stores/signup'

// The server refuses a new code within a minute of the last one.
const RESEND_COOLDOWN_SECONDS = 60

const router = useRouter()
const signupStore = useSignupStore()

// A refresh keeps the form (sessionStorage) but not the password, which is only
// ever held in memory; ask for it again rather than store it.
const needsPassword = !signupStore.password
// The consent chosen on the form is kept across a refresh; if it did not come
// back intact, it is asked for here instead of being assumed.
const needsConsent = !signupStore.consent
const consentRef = ref<InstanceType<typeof LegalConsent> | null>(null)

const { handleSubmit, defineField } = useForm({
  validationSchema: computed(() =>
    toTypedSchema(
      z.object({
        otp: z
          .string()
          .length(6, { message: t('account.verifyEmail.codeInvalid') })
          .default(''),
        password: needsPassword ? z.string().min(1) : z.string().optional(),
      })
    )
  ),
})

const [otp, otpProps] = defineField('otp', vuetifyConfig)
const [password, passwordProps] = defineField('password', vuetifyConfig)
const error = ref('')

// Validation only; the request is sent by `submit` below. The form is not
// "submitting" while the consent prompt waits for an answer, so the button
// shows loading only once the request is really on its way.
const validated = handleSubmit((value) => value)
const submitting = ref(false)

const submit = async () => {
  if (submitting.value) return
  const value = await validated()
  if (!value) return
  error.value = ''
  if (needsConsent) {
    const consent = await consentRef.value?.confirm()
    if (!consent) return
    signupStore.consent = consent
  }
  if (needsPassword && value.password) signupStore.password = value.password
  submitting.value = true
  try {
    const { data } = await signupStore.signup(value.otp)
    // Registration answers with a session, the same as signing in: the person
    // has just chosen the password, so asking for it again would be busywork.
    await AccountService.login(data.accessToken, data.user)
    toast.success(t('account.verifyEmail.accountCreated'))
    router.replace('/')
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.verifyEmail.failed'))
  } finally {
    submitting.value = false
  }
}

const handleOtpInput = (value: string) => {
  if (value.length === 6 && (!needsPassword || password.value)) {
    submit()
  }
}

const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined
const resendWait = computed(() =>
  Math.max(0, RESEND_COOLDOWN_SECONDS - Math.floor((now.value - signupStore.codeSentAt) / 1000))
)
const resending = ref(false)

const handleResend = async () => {
  error.value = ''
  resending.value = true
  try {
    await signupStore.resendCode()
    now.value = Date.now()
    toast.success(t('account.verifyEmail.resent'))
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.verifyEmail.resendFailed'))
  } finally {
    resending.value = false
  }
}

onMounted(() => {
  // Opened directly, or after the session ended: there is nothing to verify.
  if (!signupStore.email) {
    router.replace({ name: 'SignUpStart' })
    return
  }
  ticker = setInterval(() => (now.value = Date.now()), 1000)
})

onBeforeUnmount(() => clearInterval(ticker))
</script>
