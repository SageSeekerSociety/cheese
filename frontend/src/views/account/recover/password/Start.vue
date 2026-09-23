<template>
  <div>
    <AccountHeading :title="t('account.recover.title')" :lede="t('account.recover.lede')" />

    <v-alert v-if="myAlert.message" :type="myAlert.type" variant="tonal" density="comfortable" class="mb-6">
      {{ myAlert.message }}
    </v-alert>

    <v-form @submit.prevent="submit">
      <v-text-field
        id="field-email"
        v-model="email"
        autocomplete="email"
        name="email"
        type="email"
        :label="t('account.recover.email')"
        v-bind="emailProps"
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
        {{ t('account.recover.submit') }}
      </v-btn>

      <p class="text-body-2" style="color: var(--muted)">
        {{ t('account.recover.rememberPassword') }}
        <v-btn
          variant="text"
          color="primary"
          to="/account/signin"
          style="text-transform: none; padding: 0; min-width: auto; height: auto; vertical-align: baseline"
          class="text-decoration-none"
        >
          {{ t('account.backToSignIn') }}
        </v-btn>
      </p>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import { computed, ref } from 'vue'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'

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
      z.object({
        email: z.string().email(),
      })
    )
  ),
})

const [email, emailProps] = defineField('email', vuetifyConfig)

const submit = handleSubmit(async (value) => {
  try {
    await UserApi.recoverPasswordRequest(value.email)
    myAlert.value = {
      message: t('account.recover.sent'),
      type: 'success',
    }
  } catch (e) {
    myAlert.value = {
      message: requestErrorMessage(e, t('account.recover.failed')),
      type: 'error',
    }
  }
})
</script>
