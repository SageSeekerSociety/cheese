<!--
  What asking for a sign-in code by mail shows (Request.vue sends the address
  and holds the form back while the server says to wait): the address field
  with its validation, the send button, and the way back to signing in.
-->
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
          autocapitalize="none"
          autocorrect="off"
          spellcheck="false"
          name="email"
          type="email"
          v-bind="emailProps"
        />
      </AccountField>

      <BaseButton
        block
        kind="primary"
        size="lg"
        type="submit"
        class="account-submit"
        :loading="submitting"
        :disabled="waiting"
      >
        {{ t('account.emailCode.send') }}
      </BaseButton>

      <p class="account-foot">
        <NavLink :to="backToSignIn" class="account-link account-link--quiet">
          {{ t('account.backToSignIn') }}
        </NavLink>
      </p>
    </v-form>
  </div>
</template>

<script lang="ts" setup>
import type { NavTarget } from '@/lib/navTarget'

import { computed } from 'vue'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** The address last asked for, so a refresh does not lose it. */
  initialEmail: string
  /** The sentence for a send that failed, or '' while there is none. */
  error: string
  /** The server asked to wait: the button stays out of reach. */
  waiting: boolean
  /** The container is waiting on the server. */
  submitting: boolean
  /** Where the way back to signing in goes, keeping where the sign-in was headed. */
  backToSignIn: NavTarget
}>()

const emit = defineEmits<{ submit: [email: string] }>()

const { handleSubmit, defineField } = useForm({
  validationSchema: computed(() => toTypedSchema(z.object({ email: z.string().email() }))),
  initialValues: { email: props.initialEmail },
})

const [email, emailProps] = defineField('email', vuetifyConfig)

const submit = handleSubmit((value) => {
  emit('submit', value.email)
})
</script>
