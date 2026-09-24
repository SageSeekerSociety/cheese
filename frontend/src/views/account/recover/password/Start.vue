<template>
  <div>
    <!-- Once the mail is out the form has done its job, so it gives way to
         what happens next rather than staying up with a banner over it. -->
    <template v-if="sent">
      <AccountHeading :title="t('account.recover.sentTitle')" :lede="t('account.recover.sent')" />
      <v-btn block color="primary" size="large" to="/account/signin" class="account-submit">
        {{ t('account.backToSignIn') }}
      </v-btn>
    </template>

    <template v-else>
      <AccountHeading :title="t('account.recover.title')" :lede="t('account.recover.lede')" />

      <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
        {{ error }}
      </v-alert>

      <v-form @submit.prevent="submit">
        <AccountField :label="t('account.recover.email')" input-id="recover-email">
          <v-text-field
            id="recover-email"
            v-model="email"
            autocomplete="email"
            name="email"
            type="email"
            v-bind="emailProps"
          />
        </AccountField>

        <v-btn block color="primary" size="large" type="submit" class="account-submit" :loading="isSubmitting">
          {{ t('account.recover.submit') }}
        </v-btn>

        <p class="account-foot">
          {{ t('account.recover.rememberPassword') }}
          <router-link to="/account/signin" class="account-link">{{ t('account.backToSignIn') }}</router-link>
        </p>
      </v-form>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed, ref } from 'vue'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import { attemptMessage } from '../../attemptWait'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'

const error = ref('')
const sent = ref(false)

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
  error.value = ''
  try {
    await UserApi.recoverPasswordRequest(value.email)
    sent.value = true
  } catch (e) {
    error.value = attemptMessage(e) ?? requestErrorMessage(e, t('account.recover.failed'))
  }
})
</script>
