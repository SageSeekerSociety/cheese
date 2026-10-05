<!--
  What the add-an-address screen shows (AddEmail.vue): the address form, and,
  once a code has been sent, the code step; the address is validated here, the
  request is made by the container.
-->
<template>
  <transition name="account-page" mode="out-in">
    <EmailCodeStep
      v-if="step === 'code'"
      key="code"
      :email="email.trim()"
      :submit-label="t('account.addEmail.submit')"
      :verify="verify"
      :send="send"
      @change="emit('change')"
    />

    <div v-else key="email">
      <AccountHeading :title="t('account.addEmail.title')" :lede="t('account.addEmail.lede')" />

      <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
        {{ error }}
      </v-alert>

      <v-form ref="formRef" @submit.prevent="submitEmail">
        <AccountField :label="t('account.field.email')" input-id="add-email">
          <v-text-field
            id="add-email"
            :model-value="email"
            autocomplete="email"
            autocapitalize="none"
            autocorrect="off"
            spellcheck="false"
            type="email"
            name="email"
            :rules="emailRules"
            :hint="t('account.rule.emailHint')"
            persistent-hint
            @update:model-value="(value: string) => emit('update:email', value)"
          />
        </AccountField>

        <BaseButton type="submit" block kind="primary" size="lg" class="account-submit" :loading="sending">
          {{ t('account.addEmail.send') }}
        </BaseButton>

        <p class="account-foot">
          <button
            type="button"
            class="account-link account-link--quiet"
            :disabled="signingOut"
            @click="emit('signOut')"
          >
            {{ t('account.addEmail.signOut') }}
          </button>
        </p>
      </v-form>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import EmailCodeStep from './EmailCodeStep.vue'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  step: 'email' | 'code'
  email: string
  error: string
  sending: boolean
  signingOut: boolean
  /** Checks the code; a rejection is shown by the code step. */
  verify: (code: string) => Promise<void>
  /** Sends another code to the same address. */
  send: () => Promise<unknown>
}>()

const emit = defineEmits<{
  'update:email': [email: string]
  submitEmail: []
  signOut: []
  change: []
}>()

const formRef = ref()
const emailRules = [(v: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim()) || t('account.rule.emailInvalid')]

// Only the address form is validated here; the request is made by the container.
const submitEmail = async () => {
  const { valid } = await formRef.value.validate()
  if (!valid) return
  emit('submitEmail')
}
</script>
