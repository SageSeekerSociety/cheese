<template>
  <div>
    <AccountHeading :title="t('account.signUp.title')" />

    <v-alert v-if="error" closable type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form ref="signupForm" @submit.prevent="submit">
      <v-text-field
        id="signup-username"
        v-model="username"
        name="username"
        autocomplete="username"
        :label="t('account.field.username')"
        v-bind="usernameProps"
      />

      <v-text-field
        id="signup-nickname"
        v-model="nickname"
        name="nickname"
        autocomplete="nickname"
        :label="t('account.field.displayName')"
        v-bind="nicknameProps"
      />

      <v-text-field
        id="signup-email"
        v-model="email"
        name="email"
        autocomplete="email"
        :label="t('account.field.email')"
        type="email"
        :hint="t('account.rule.emailHint')"
        persistent-hint
        v-bind="emailProps"
        class="mb-2"
      />

      <PasswordField
        id="signup-password"
        v-model="password"
        name="password"
        autocomplete="new-password"
        :label="t('account.field.password')"
        :hint="t('account.rule.passwordHint')"
        persistent-hint
        v-bind="passwordProps"
        class="mb-2"
      />

      <PasswordField
        id="signup-confirm-password"
        v-model="confirmPassword"
        name="confirmPassword"
        autocomplete="new-password"
        :label="t('account.field.confirmPassword')"
        v-bind="confirmPasswordProps"
      />

      <v-text-field
        v-if="requireInviteCode"
        v-model="inviteCode"
        autocomplete="off"
        :label="t('account.invitationCode')"
        v-bind="inviteCodeProps"
      />

      <LegalConsent ref="consentRef" :action-label="t('account.agreeAndSignUp')" class="mb-6" />

      <v-btn
        block
        color="primary"
        size="large"
        type="submit"
        :loading="isSubmitting"
        :disabled="!registrationConfigReady"
        style="text-transform: none; font-weight: 500; height: 48px"
        class="mb-4"
      >
        {{ t('account.signUp.submit') }}
      </v-btn>

      <p class="text-body-2" style="color: var(--muted)">
        {{ t('account.signUp.haveAccount') }}
        <v-btn
          variant="text"
          color="primary"
          to="/account/signin"
          style="text-transform: none; padding: 0; min-width: auto; height: auto; vertical-align: baseline"
          class="text-decoration-none"
          >{{ t('account.signUp.signIn') }}</v-btn
        >
      </p>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { REGEX_PASSWORD, REGEX_USERNAME, vuetifyConfig } from '@/utils/form'

import AccountHeading from '@/components/account/AccountHeading.vue'
import LegalConsent from '@/components/account/LegalConsent.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { useSignupStore } from '@/stores/signup'

const error = ref('')
const requireInviteCode = ref(false)
const registrationConfigReady = ref(false)

const { handleSubmit, defineField, isSubmitting } = useForm({
  validationSchema: computed(() =>
    toTypedSchema(
      z
        .object({
          username: z.string().regex(REGEX_USERNAME, { message: t('account.rule.username') }),
          nickname: z
            .string()
            .min(1)
            .max(50)
            .regex(/^[a-zA-Z0-9_\u4e00-\u9fa5]{1,50}$/, {
              message: t('account.rule.displayName'),
            }),
          password: z.string().regex(REGEX_PASSWORD, { message: t('account.rule.passwordInvalid') }),
          confirmPassword: z.string().min(1),
          email: z.string().email(),
          inviteCode: z.string().optional(),
        })
        .superRefine(({ password, confirmPassword, inviteCode }, ctx) => {
          if (password !== confirmPassword) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['confirmPassword'],
              message: t('account.rule.passwordsDoNotMatch'),
            })
          }
          if (requireInviteCode.value && !inviteCode?.trim()) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['inviteCode'],
              message: t('account.enterAnInvitationCode'),
            })
          }
        })
    )
  ),
})

const [username, usernameProps] = defineField('username', vuetifyConfig)
const [nickname, nicknameProps] = defineField('nickname', vuetifyConfig)
const [password, passwordProps] = defineField('password', vuetifyConfig)
const [confirmPassword, confirmPasswordProps] = defineField('confirmPassword', vuetifyConfig)
const [email, emailProps] = defineField('email', vuetifyConfig)
const [inviteCode, inviteCodeProps] = defineField('inviteCode', vuetifyConfig)
const consentRef = ref<InstanceType<typeof LegalConsent> | null>(null)

const signupStore = useSignupStore()
const router = useRouter()

onMounted(async () => {
  try {
    const { data } = await UserApi.getRegistrationConfig()
    requireInviteCode.value = data.requireInviteCode
    registrationConfigReady.value = true
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.registrationSettingsCouldNotBeLoadedRefresh'))
  }
})

const submit = handleSubmit(async (value) => {
  error.value = ''
  const consent = await consentRef.value?.confirm()
  if (!consent) return
  try {
    await signupStore.startSignup({
      ...value,
      inviteCode: requireInviteCode.value ? value.inviteCode?.trim() : undefined,
      consent,
    })

    router.push('/account/signup/verify-email')
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.signUp.failed'))
  }
})
</script>
