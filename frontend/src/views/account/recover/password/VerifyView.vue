<!--
  What setting the new password a reset link carries shows (Verify.vue reads
  the token and sends it to the server): a new password entered twice, with the
  account's name riding along so a password manager files it under the right
  account.
-->
<template>
  <div>
    <AccountHeading :title="t('account.resetPassword.title')" />

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form @submit.prevent="submit">
      <!-- The account's name rides along, hidden, so a password manager files
           the new password under the right account. -->
      <input type="text" name="username" autocomplete="username" :value="username" hidden />

      <AccountField :label="t('account.field.newPassword')" input-id="reset-password">
        <PasswordField
          id="reset-password"
          v-model="password"
          autocomplete="new-password"
          name="password"
          :hint="t('account.rule.passwordHint')"
          persistent-hint
          v-bind="passwordProps"
        />
      </AccountField>

      <AccountField :label="t('account.field.confirmPassword')" input-id="reset-confirm-password">
        <PasswordField
          id="reset-confirm-password"
          v-model="confirmPassword"
          autocomplete="new-password"
          name="confirmPassword"
          v-bind="confirmPasswordProps"
        />
      </AccountField>

      <BaseButton block kind="primary" size="lg" type="submit" class="account-submit" :loading="submitting">
        {{ t('account.resetPassword.submit') }}
      </BaseButton>

      <p class="account-foot">
        <router-link to="/account/signin" class="account-link account-link--quiet">
          {{ t('account.backToSignIn') }}
        </router-link>
      </p>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { REGEX_PASSWORD, vuetifyConfig } from '@/utils/form'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** The account the reset token names, for the hidden username field. */
  username: string | undefined
  /** The sentence for a reset that failed, or '' while there is none. */
  error: string
  /** The container is waiting on the server. */
  submitting: boolean
}>()

const emit = defineEmits<{ submit: [password: string] }>()

const { handleSubmit, defineField } = useForm({
  validationSchema: computed(() =>
    toTypedSchema(
      z
        .object({
          password: z.string().regex(REGEX_PASSWORD, { message: t('account.rule.passwordInvalid') }),
          confirmPassword: z.string().min(1),
        })
        .superRefine(({ password, confirmPassword }, ctx) => {
          if (password !== confirmPassword) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['confirmPassword'],
              message: t('account.rule.passwordsDoNotMatch'),
            })
          }
        })
    )
  ),
})

const [password, passwordProps] = defineField('password', vuetifyConfig)
const [confirmPassword, confirmPasswordProps] = defineField('confirmPassword', vuetifyConfig)

const submit = handleSubmit((value) => {
  emit('submit', value.password)
})
</script>
