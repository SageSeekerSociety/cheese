<template>
  <div>
    <AccountHeading :title="t('account.resetPassword.title')" />

    <v-alert v-if="myAlert.message" :type="myAlert.type" variant="tonal" density="comfortable" class="mb-6">
      {{ myAlert.message }}
    </v-alert>

    <v-form @submit.prevent="submit">
      <PasswordField
        id="field-password"
        v-model="password"
        autocomplete="new-password"
        name="password"
        :label="t('account.field.newPassword')"
        :hint="t('account.rule.passwordHint')"
        persistent-hint
        v-bind="passwordProps"
        class="mb-2"
      />

      <PasswordField
        id="field-confirmPassword"
        v-model="confirmPassword"
        autocomplete="new-password"
        name="confirmPassword"
        :label="t('account.field.confirmPassword')"
        v-bind="confirmPasswordProps"
        class="mb-2"
      />

      <v-btn
        block
        color="primary"
        size="large"
        type="submit"
        :loading="isSubmitting"
        style="text-transform: none; font-weight: 500; height: 48px"
        class="mb-4"
      >
        {{ t('account.resetPassword.submit') }}
      </v-btn>

      <v-btn
        variant="text"
        color="primary"
        to="/account/signin"
        style="text-transform: none; padding: 0; min-width: auto"
        class="text-decoration-none"
      >
        {{ t('account.backToSignIn') }}
      </v-btn>
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

const myAlert = ref<{
  message: string | undefined
  type: 'success' | 'info' | 'warning' | 'error' | undefined
}>({
  message: '',
  type: 'error',
})

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
  if (!username.value) {
    myAlert.value = { message: t('account.resetPassword.invalidLink'), type: 'error' }
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
    myAlert.value = {
      message: requestErrorMessage(e, t('account.resetPassword.failed')),
      type: 'error',
    }
  }
})
</script>
