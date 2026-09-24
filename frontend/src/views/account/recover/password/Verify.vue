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

      <v-btn block color="primary" size="large" type="submit" class="account-submit" :loading="isSubmitting">
        {{ t('account.resetPassword.submit') }}
      </v-btn>

      <p class="account-foot">
        <router-link to="/account/signin" class="account-link account-link--quiet">
          {{ t('account.backToSignIn') }}
        </router-link>
      </p>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import type { TokenPayload } from '@/network/api/users/types'
import type { SignInNoticeKey } from '../../signInNotice'

import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toTypedSchema } from '@vee-validate/zod'
import { jwtDecode } from 'jwt-decode'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { REGEX_PASSWORD, vuetifyConfig } from '@/utils/form'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'

const route = useRoute()
const token = computed(() => route.query.token as string)

// 从 token 中解析用户名
const username = computed(() => {
  try {
    const { payload } = jwtDecode<TokenPayload>(token.value)
    return payload.authorization.username
  } catch {
    return undefined
  }
})

const error = ref('')

const { handleSubmit, defineField, isSubmitting } = useForm({
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

const router = useRouter()

const submit = handleSubmit(async (value) => {
  error.value = ''
  if (!username.value) {
    error.value = t('account.resetPassword.invalidLink')
    return
  }
  try {
    await UserApi.recoverPasswordVerify({
      token: token.value,
      password: value.password,
    })

    const message: SignInNoticeKey = 'passwordReset'
    router.replace({ name: 'SignIn', query: { username: username.value, message } })
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.resetPassword.failed'))
  }
})
</script>
