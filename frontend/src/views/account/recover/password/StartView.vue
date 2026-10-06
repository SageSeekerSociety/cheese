<!--
  What asking for a password reset shows (Start.vue sends the address): the
  address field with its validation, the way back to signing in, and, once the
  mail is out, the confirmation that replaces the form.
-->
<template>
  <div>
    <!-- Once the mail is out the form has done its job, so it gives way to
         what happens next rather than staying up with a banner over it. -->
    <template v-if="sent">
      <AccountHeading :title="t('account.recover.sentTitle')" :lede="t('account.recover.sent')" />
      <BaseButton block kind="primary" size="lg" to="/account/signin" class="account-submit">
        {{ t('account.backToSignIn') }}
      </BaseButton>
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
            autocapitalize="none"
            autocorrect="off"
            spellcheck="false"
            name="email"
            type="email"
            v-bind="emailProps"
          />
        </AccountField>

        <BaseButton block kind="primary" size="lg" type="submit" class="account-submit" :loading="submitting">
          {{ t('account.recover.submit') }}
        </BaseButton>

        <p class="account-foot">
          {{ t('account.recover.rememberPassword') }}
          <router-link to="/account/signin" class="account-link">{{ t('account.backToSignIn') }}</router-link>
        </p>
      </v-form>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** The sentence for a send that failed, or '' while there is none. */
  error: string
  /** The mail is out: the form has given way to the confirmation. */
  sent: boolean
  /** The container is waiting on the server. */
  submitting: boolean
}>()

const emit = defineEmits<{ submit: [email: string] }>()

const { handleSubmit, defineField } = useForm({
  validationSchema: computed(() =>
    toTypedSchema(
      z.object({
        email: z.string().email(),
      })
    )
  ),
})

const [email, emailProps] = defineField('email', vuetifyConfig)

const submit = handleSubmit((value) => {
  emit('submit', value.email)
})
</script>
