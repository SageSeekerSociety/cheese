<template>
  <div>
    <AccountHeading :title="t('account.emailCode.title')" />

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form @submit.prevent="submit">
      <AccountField :label="t('account.field.email')" input-id="email-code-email">
        <v-text-field
          id="email-code-email"
          v-model="email"
          autocomplete="email"
          name="email"
          type="email"
          v-bind="emailProps"
        />
      </AccountField>

      <v-btn
        block
        color="primary"
        size="large"
        type="submit"
        class="account-submit"
        :loading="isSubmitting"
        :disabled="waiting"
      >
        {{ t('account.emailCode.send') }}
      </v-btn>

      <p class="account-foot">
        <router-link :to="backToSignIn" class="account-link account-link--quiet">
          {{ t('account.backToSignIn') }}
        </router-link>
      </p>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import { attemptMessage, useAttemptWait } from '../attemptWait'

import { pendingCode, rememberCodeSent } from './pendingCode'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'

const router = useRouter()
const route = useRoute()

// The way back keeps where the sign-in was headed.
const backToSignIn = computed(() => ({ name: 'SignIn', query: { redirect: route.query.redirect } }))

const { handleSubmit, defineField, isSubmitting } = useForm({
  validationSchema: computed(() => toTypedSchema(z.object({ email: z.string().email() }))),
  initialValues: { email: pendingCode()?.email ?? '' },
})

const [email, emailProps] = defineField('email', vuetifyConfig)
const error = ref('')
const { waiting, waitFor } = useAttemptWait()

const submit = handleSubmit(async (value) => {
  if (waiting.value) return
  error.value = ''
  const address = value.email.trim()
  try {
    await UserApi.requestSignInCode(address)
    rememberCodeSent(address)
    router.push({ name: 'SignInEmailCodeVerify', query: { redirect: route.query.redirect } })
  } catch (e) {
    error.value = attemptMessage(e) ?? requestErrorMessage(e, t('account.verifyEmail.resendFailed'))
    waitFor(e)
  }
})
</script>
