<!--
  Asking for a password reset: the address is sent to the server and the form
  gives way to the mail-is-out confirmation. What it shows is StartView.vue.
-->
<template>
  <StartView :error="error" :sent="sent" :submitting="submitting" @submit="submit" />
</template>

<script lang="ts" setup>
import { ref } from 'vue'

import StartView from './StartView.vue'

import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'

const error = ref('')
const sent = ref(false)
const submitting = ref(false)

const submit = async (email: string) => {
  error.value = ''
  submitting.value = true
  try {
    await UserApi.recoverPasswordRequest(email)
    sent.value = true
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.recover.failed'))
  } finally {
    submitting.value = false
  }
}
</script>
