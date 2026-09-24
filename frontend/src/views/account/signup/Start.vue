<template>
  <div>
    <AccountHeading :title="t('account.signUp.title')">
      {{ t('account.signUp.haveAccount') }}
      <router-link to="/account/signin" class="account-link">{{ t('account.signUp.signIn') }}</router-link>
    </AccountHeading>

    <v-alert v-if="error" closable type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form ref="signupForm" @submit.prevent="submit">
      <AccountField :label="t('account.field.username')" input-id="signup-username">
        <v-text-field
          id="signup-username"
          v-model="username"
          name="username"
          autocomplete="username"
          v-bind="usernameProps"
        />
      </AccountField>

      <AccountField :label="t('account.field.displayName')" input-id="signup-nickname">
        <v-text-field
          id="signup-nickname"
          v-model="nickname"
          name="nickname"
          autocomplete="nickname"
          v-bind="nicknameProps"
        />
      </AccountField>

      <AccountField :label="t('account.field.email')" input-id="signup-email">
        <v-text-field
          id="signup-email"
          v-model="email"
          name="email"
          autocomplete="email"
          type="email"
          :hint="t('account.rule.emailHint')"
          persistent-hint
          v-bind="emailProps"
        />
      </AccountField>

      <AccountField :label="t('account.field.password')" input-id="signup-password">
        <PasswordField
          id="signup-password"
          v-model="password"
          name="password"
          autocomplete="new-password"
          :hint="t('account.rule.passwordHint')"
          persistent-hint
          v-bind="passwordProps"
        />
      </AccountField>

      <AccountField :label="t('account.field.confirmPassword')" input-id="signup-confirm-password">
        <PasswordField
          id="signup-confirm-password"
          v-model="confirmPassword"
          name="confirmPassword"
          autocomplete="new-password"
          v-bind="confirmPasswordProps"
        />
      </AccountField>

      <AccountField v-if="requireInviteCode" :label="t('account.invitationCode')" input-id="signup-invite-code">
        <v-text-field id="signup-invite-code" v-model="inviteCode" autocomplete="off" v-bind="inviteCodeProps" />
      </AccountField>

      <LegalConsent ref="consentRef" :action-label="t('account.agreeAndSignUp')" class="mb-4" />

      <v-btn
        block
        color="primary"
        size="large"
        type="submit"
        class="account-submit"
        :loading="submitting"
        :disabled="!registrationConfigReady"
      >
        {{ t('account.signUp.submit') }}
      </v-btn>
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

import AccountField from '@/components/account/AccountField.vue'
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

const { handleSubmit, defineField } = useForm({
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

// Validation only; the request is sent by `submit` below. The form is not
// "submitting" while the consent prompt waits for an answer, so the button
// shows loading only once the email code is really being requested.
const validated = handleSubmit((value) => value)
const submitting = ref(false)

const submit = async () => {
  if (submitting.value) return
  const value = await validated()
  if (!value) return
  error.value = ''
  const consent = await consentRef.value?.confirm()
  if (!consent) return
  submitting.value = true
  try {
    await signupStore.startSignup({
      ...value,
      inviteCode: requireInviteCode.value ? value.inviteCode?.trim() : undefined,
      consent,
    })

    router.push('/account/signup/verify-email')
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.signUp.failed'))
  } finally {
    submitting.value = false
  }
}
</script>
