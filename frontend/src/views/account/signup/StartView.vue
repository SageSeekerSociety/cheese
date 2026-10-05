<!--
  What creating an account shows (Start.vue loads the registration settings and
  the agreements, then sends the form): the fields with their validation, the
  agreement box and the way back to signing in.
-->
<template>
  <div>
    <AccountHeading :title="t('account.signUp.title')">
      {{ t('account.signUp.haveAccount') }}
      <router-link to="/account/signin" class="account-link">{{ t('account.signUp.signIn') }}</router-link>
    </AccountHeading>

    <v-alert v-if="error" closable type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form @submit.prevent="submit">
      <AccountField :label="t('account.field.username')" input-id="signup-username">
        <v-text-field
          id="signup-username"
          v-model="username"
          name="username"
          autocomplete="username"
          autocapitalize="none"
          autocorrect="off"
          spellcheck="false"
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
          autocapitalize="none"
          autocorrect="off"
          spellcheck="false"
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
        <v-text-field
          id="signup-invite-code"
          v-model="inviteCode"
          autocomplete="off"
          autocapitalize="none"
          autocorrect="off"
          spellcheck="false"
          v-bind="inviteCodeProps"
        />
      </AccountField>

      <LegalConsent
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
        :disabled="!registrationConfigReady"
      >
        {{ t('account.signUp.submit') }}
      </BaseButton>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import type { AcceptedDocuments } from '@/network/api/legal/types'

import { computed, ref } from 'vue'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { REGEX_PASSWORD, REGEX_USERNAME, vuetifyConfig } from '@/utils/form'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import LegalConsent from '@/components/account/LegalConsent.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

export type SignUpValues = {
  username: string
  nickname: string
  password: string
  confirmPassword: string
  email: string
  inviteCode?: string
}

const props = defineProps<{
  /** The sentence for a registration that failed, or '' while there is none. */
  error: string
  /** The server wants an invitation code, so the field is shown and required. */
  requireInviteCode: boolean
  /** The settings are in: the submit button is out of reach until they are. */
  registrationConfigReady: boolean
  /** The container is waiting on the server. */
  submitting: boolean
  /** The agreement versions to be agreed to, or null before they load. */
  consentDocuments: AcceptedDocuments | null
  /** The sentence for agreements that could not be loaded, or '' while there is none. */
  consentLoadError: string
}>()

const emit = defineEmits<{ submit: [value: SignUpValues] }>()

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
          if (props.requireInviteCode && !inviteCode?.trim()) {
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

const submit = handleSubmit((value) => {
  emit('submit', value)
})

/** Asked by the container at submit time: the agreements this run will hand the server. */
const confirmConsent = () => consentRef.value?.confirm() ?? Promise.resolve(null)
defineExpose({ confirmConsent })
</script>
