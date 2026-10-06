<!--
  What the sign-in page shows (SignIn.vue): the notice or error, the ways in,
  and the username/password form. It draws them from the props the container
  hands it, reports the way that was picked, and emits the form when it is sent.
-->
<template>
  <div>
    <AccountHeading :title="t('account.signIn.title')">
      {{ t('account.signIn.noAccount') }}
      <NavLink to="signup" class="account-link">{{ t('account.signIn.createAccount') }}</NavLink>
    </AccountHeading>

    <v-alert v-if="errorMessage" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorMessage }}
    </v-alert>
    <v-alert v-else-if="notice" type="success" variant="tonal" density="comfortable" class="mb-6">
      {{ notice }}
    </v-alert>

    <!-- Whichever way this browser last used goes first; with no history, the
         one-click ways lead and the password form follows. Rendered in that
         order, not reordered by CSS, so the keyboard walks it the same way. -->
    <template v-for="part in parts" :key="part">
      <div v-if="part === 'alt'" class="signin-alt">
        <BaseButton
          v-for="way in alternatives"
          :key="way.key"
          block
          kind="secondary"
          size="lg"
          class="signin-alt__btn"
          :prepend-icon="way.icon"
          :loading="busy === way.key"
          :disabled="!!busy && busy !== way.key"
          @click="emit('alt', way.key)"
        >
          {{ way.label }}
          <span v-if="way.key === last" class="signin-alt__last">{{ t('account.signIn.lastUsed') }}</span>
        </BaseButton>
      </div>

      <div v-else-if="part === 'or'" class="signin-or">{{ t('account.signIn.or') }}</div>

      <v-form v-else @submit.prevent="submit">
        <AccountField :label="t('account.field.username')" input-id="signin-username">
          <!-- `webauthn` lets the browser offer this device's passkeys right in
               the field's suggestions. -->
          <v-text-field
            id="signin-username"
            v-model="username"
            name="username"
            autocomplete="username webauthn"
            autocapitalize="none"
            autocorrect="off"
            spellcheck="false"
            v-bind="usernameProps"
          />
        </AccountField>

        <AccountField :label="t('account.field.password')" input-id="signin-password">
          <template #aside>
            <NavLink to="recover/password" class="account-link account-link--quiet">
              {{ t('account.signIn.forgotPassword') }}
            </NavLink>
          </template>
          <PasswordField
            id="signin-password"
            v-model="password"
            name="password"
            autocomplete="current-password"
            v-bind="passwordProps"
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
          {{ t('account.signIn.submit') }}
        </BaseButton>
      </v-form>
    </template>

    <!-- Signing in does not create an account (both sign-up entries ask for
         consent in their own place), so this is a notice, not a checkbox
         (#1486). Below every way in, it holds for all of them. -->
    <p class="account-fine">
      {{ t('account.signInMeansYouAgreeTo') }}
      <LegalLinks />
    </p>
  </div>
</template>

<script setup lang="ts">
import type { SignInMethod } from './lastSignIn'

import { computed, watch } from 'vue'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import LegalLinks from '@/components/account/LegalLinks.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'

/** One way in, as the container worked it out: which way, what it reads, its icon. */
export interface SignInWay {
  key: SignInMethod
  label: string
  icon: string
}

/** What the form was filled with when it was sent. */
export interface SignInCredentials {
  username: string
  password: string
}

const props = defineProps<{
  errorMessage: string
  /** A notice named by the link, or null for none. */
  notice: string | null
  /** The ways in and the form, in the order they are drawn. */
  parts: readonly ('alt' | 'or' | 'form')[]
  alternatives: SignInWay[]
  /** The way this browser last used, marked and led with; null with no history. */
  last: SignInMethod | null
  /** The way in progress, so the others wait for it. */
  busy: SignInMethod | null
  /** A sign-in is being sent. */
  submitting: boolean
  /** A refused attempt is being waited out; the form is held back until it passes. */
  waiting: boolean
  /** A name the route asked to start with, filled into the field once. */
  initialUsername: string
}>()

const emit = defineEmits<{
  alt: [key: SignInMethod]
  submit: [value: SignInCredentials]
}>()

// Signing in names an existing account, so only presence is checked here: the
// server is the one that knows whether the name and password are right.
const { handleSubmit, defineField } = useForm({
  validationSchema: computed(() =>
    toTypedSchema(
      z.object({
        username: z.string().min(1),
        password: z.string().min(1),
      })
    )
  ),
})

const [username, usernameProps] = defineField('username', vuetifyConfig)
const [password, passwordProps] = defineField('password', vuetifyConfig)

// The route asked for a name to start with; set it once, as the page opens.
watch(
  () => props.initialUsername,
  (value) => {
    if (value) username.value = value
  },
  { immediate: true }
)

const submit = handleSubmit((value) => emit('submit', value))
</script>

<style scoped>
.signin-alt {
  display: grid;
  gap: 10px;
}

.signin-alt__btn {
  position: relative;
  border-color: var(--line-2);
}

.signin-alt__last {
  position: absolute;
  top: 50%;
  right: 10px;
  padding: 1px 7px;
  font-size: 12px;
  font-weight: 500;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill-2);
  border-radius: var(--radius-sm);
  transform: translateY(-50%);
}

.signin-or {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 24px 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.signin-or::before,
.signin-or::after {
  flex: 1;
  height: 1px;
  content: '';
  background: var(--line);
}
</style>
