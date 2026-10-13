<!--
  What the OAuth verify screen shows (OAuthVerify.vue): the address being
  bound, the account's password, and the two answers a wrong one can draw —
  a message on the field or a general alert above the form.
-->
<template>
  <div>
    <AccountHeading :title="t('account.oauth.verify.title')" :lede="t('account.oauth.verify.lede', { email })" />

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ error }}
    </v-alert>

    <v-form ref="formRef" @submit.prevent="submit">
      <AccountField :label="t('account.field.password')" input-id="oauth-verify-password">
        <PasswordField
          id="oauth-verify-password"
          v-model="password"
          autocomplete="current-password"
          name="password"
          :rules="passwordRules"
          :error-messages="errorMessage"
          required
        />
      </AccountField>

      <BaseButton type="submit" block kind="primary" size="lg" class="account-submit" :loading="loading">
        {{ t('account.oauth.verify.submit') }}
      </BaseButton>

      <p class="account-foot">
        <NavLink to="/account/signin" class="account-link account-link--quiet">
          {{ t('account.backToSignIn') }}
        </NavLink>
      </p>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import { ref } from 'vue'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'

defineProps<{
  /** The address the provider account is being bound to. */
  email: string
  /** A general failure, shown above the form. */
  error: string
  /** A wrong password, shown on the field itself. */
  errorMessage: string
  loading: boolean
}>()

const emit = defineEmits<{ submit: [password: string] }>()

const formRef = ref()
const password = ref('')

const passwordRules = [(v: string) => !!v || t('account.oauth.verify.passwordRequired')]

// Only the form is validated here; the password is handed up and the request
// is made by the container.
const submit = async () => {
  const { valid } = await formRef.value.validate()
  if (!valid) return
  emit('submit', password.value)
}
</script>
