<!--
  What the third-party sign-up page shows (OAuthComplete.vue): the create or
  bind form, its fields and rules, and the consent prompt. It draws them from
  the props the container hands it, holds the fields it is filled with, and
  reports a form as it is sent; the calls it makes are the ones passed in.
-->
<template>
  <div>
    <v-progress-linear v-if="loading" indeterminate color="primary" height="2" />

    <transition v-else-if="oauthState" name="account-page" mode="out-in">
      <EmailCodeStep
        v-if="step === 'code'"
        key="code"
        :email="createEmail.trim()"
        :submit-label="t('account.verifyEmail.submit')"
        :verify="verifyEmail"
        :send="sendCode"
        @change="emit('change')"
      />

      <div v-else key="form">
        <AccountHeading
          :title="t('account.oauth.complete.title', { name: oauthState.userInfo.name || oauthState.suggestedNickname })"
          :lede="t('account.oauth.complete.lede', { provider: providerName })"
        />

        <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-6">
          {{ error }}
        </v-alert>

        <v-btn-toggle
          v-model="selectedOption"
          mandatory
          divided
          variant="outlined"
          color="on-surface"
          class="oauth-choice"
        >
          <!-- eslint-disable-next-line vue/no-restricted-syntax -- a segment of v-btn-toggle, not one of the BaseButton roles -->
          <v-btn value="create">{{ t('account.oauth.complete.create') }}</v-btn>
          <!-- eslint-disable-next-line vue/no-restricted-syntax -- a segment of v-btn-toggle, not one of the BaseButton roles -->
          <v-btn value="bind">{{ t('account.oauth.complete.bind') }}</v-btn>
        </v-btn-toggle>

        <transition name="account-page" mode="out-in">
          <v-form v-if="selectedOption === 'create'" key="create" ref="createFormRef" @submit.prevent="submitCreate">
            <AccountField :label="t('account.field.username')" input-id="oauth-create-username">
              <v-text-field
                id="oauth-create-username"
                v-model="createUsername"
                autocomplete="username"
                autocapitalize="none"
                autocorrect="off"
                spellcheck="false"
                name="createUsername"
                :rules="usernameRules"
                :hint="t('account.rule.username')"
                persistent-hint
              />
            </AccountField>

            <AccountField :label="t('account.field.displayName')" input-id="oauth-create-nickname">
              <v-text-field
                id="oauth-create-nickname"
                v-model="createNickname"
                autocomplete="nickname"
                name="createNickname"
                :rules="nicknameRules"
              />
            </AccountField>

            <AccountField :label="t('account.field.email')" input-id="oauth-create-email">
              <v-text-field
                id="oauth-create-email"
                v-model="createEmail"
                autocomplete="email"
                autocapitalize="none"
                autocorrect="off"
                spellcheck="false"
                type="email"
                name="createEmail"
                :rules="emailRules"
                :hint="t('account.rule.emailHint')"
                persistent-hint
              />
            </AccountField>

            <AccountField v-if="requireInviteCode" :label="t('account.invitationCode')" input-id="oauth-create-invite">
              <v-text-field
                id="oauth-create-invite"
                v-model="createInviteCode"
                autocomplete="off"
                autocapitalize="none"
                autocorrect="off"
                spellcheck="false"
                name="createInviteCode"
                :rules="inviteCodeRules"
              />
            </AccountField>

            <v-checkbox v-model="setPassword" density="compact" hide-details class="oauth-set-password">
              <template #label>
                <span class="oauth-set-password__label">{{ t('account.oauth.complete.setPassword') }}</span>
              </template>
            </v-checkbox>
            <p class="account-hint">{{ t('account.oauth.complete.setPasswordHint') }}</p>

            <template v-if="setPassword">
              <AccountField :label="t('account.field.password')" input-id="oauth-create-password">
                <PasswordField
                  id="oauth-create-password"
                  v-model="createPassword"
                  autocomplete="new-password"
                  name="createPassword"
                  :rules="newPasswordRules"
                  :hint="t('account.rule.passwordHint')"
                  persistent-hint
                />
              </AccountField>

              <AccountField :label="t('account.field.confirmPassword')" input-id="oauth-create-confirm">
                <PasswordField
                  id="oauth-create-confirm"
                  v-model="confirmPassword"
                  autocomplete="new-password"
                  name="confirmPassword"
                  :rules="confirmPasswordRules"
                />
              </AccountField>
            </template>

            <LegalConsent
              ref="consentRef"
              :action-label="t('account.agreeAndSignUp')"
              :documents="consentDocuments"
              :load-error="consentLoadError"
              class="mb-4"
            />

            <BaseButton
              type="submit"
              block
              kind="primary"
              size="lg"
              class="account-submit"
              :loading="creating"
              :disabled="!registrationConfigReady"
            >
              {{ t('account.signUp.submit') }}
            </BaseButton>
          </v-form>

          <v-form v-else key="bind" ref="bindFormRef" @submit.prevent="submitBind">
            <AccountField :label="t('account.field.username')" input-id="oauth-bind-username">
              <v-text-field
                id="oauth-bind-username"
                v-model="bindUsername"
                autocomplete="username"
                autocapitalize="none"
                autocorrect="off"
                spellcheck="false"
                name="bindUsername"
                :rules="bindUsernameRules"
              />
            </AccountField>

            <AccountField :label="t('account.field.password')" input-id="oauth-bind-password">
              <PasswordField
                id="oauth-bind-password"
                v-model="bindPassword"
                autocomplete="current-password"
                name="bindPassword"
                :rules="bindPasswordRules"
              />
            </AccountField>

            <BaseButton type="submit" block kind="primary" size="lg" class="account-submit" :loading="binding">
              {{ t('account.oauth.complete.bindSubmit') }}
            </BaseButton>
          </v-form>
        </transition>
      </div>
    </transition>

    <template v-else>
      <AccountHeading :title="t('account.oauth.complete.unavailable')" :lede="error" />
      <BaseButton block kind="primary" size="lg" :to="{ name: 'SignIn' }" class="account-submit">
        {{ t('account.backToSignIn') }}
      </BaseButton>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { AcceptedDocuments, ConsentMethod } from '@/network/api/legal/types'
import type { OAuthState } from '@/network/api/users/types'

import { ref, watch } from 'vue'

import { REGEX_PASSWORD, REGEX_USERNAME } from '@/utils/form'

import EmailCodeStep from './EmailCodeStep.vue'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import LegalConsent from '@/components/account/LegalConsent.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

/** What the create form was filled with when it was sent. */
export interface OAuthCreateValues {
  username: string
  nickname: string
  email: string
  inviteCode: string
  setPassword: boolean
  password: string
}

/** What the bind form was filled with when it was sent. */
export interface OAuthBindValues {
  username: string
  password: string
}

const props = defineProps<{
  loading: boolean
  error: string
  /** The provider's state, once loaded; null while loading or on failure. */
  oauthState: OAuthState | null
  providerName: string
  /** Which half of proving a new account is shown. */
  step: 'form' | 'code'
  requireInviteCode: boolean
  /** The registration settings are in; the create button waits for them. */
  registrationConfigReady: boolean
  creating: boolean
  binding: boolean
  /** The agreement versions to hand the backend; null until loaded. */
  consentDocuments: AcceptedDocuments | null
  consentLoadError: string
  /** Checks a mailed code; a rejection is shown by the code step. */
  verifyEmail: (code: string) => Promise<void>
  /** Sends another code to the address the form names. */
  sendCode: () => Promise<unknown>
}>()

const emit = defineEmits<{
  create: [values: OAuthCreateValues]
  bind: [values: OAuthBindValues]
  change: []
}>()

const selectedOption = ref<'create' | 'bind'>('create')

// Create form fields
const createUsername = ref('')
const createNickname = ref('')
const setPassword = ref(false)
const createPassword = ref('')
const confirmPassword = ref('')
const createInviteCode = ref('')
const createEmail = ref('')

// Bind form fields
const bindUsername = ref('')
const bindPassword = ref('')

// The provider names suggested values; filled into the fields once it arrives.
watch(
  () => props.oauthState,
  (state) => {
    if (!state) return
    createUsername.value = state.suggestedUsername
    createNickname.value = state.suggestedNickname
    createEmail.value = state.userInfo.verifiedEmail ?? state.userInfo.email ?? ''
  },
  { immediate: true }
)

// Form refs
const createFormRef = ref()
const consentRef = ref<InstanceType<typeof LegalConsent> | null>(null)
const bindFormRef = ref()

/** Lets the container answer the consent prompt once the form checks out. */
const confirmConsent = (): Promise<{ documents: AcceptedDocuments; method: ConsentMethod } | null> =>
  consentRef.value?.confirm() ?? Promise.resolve(null)
defineExpose({ confirmConsent })

// Validation rules — the same ones the sign-up form states.
const usernameRules = [
  (v: string) => !!v || t('account.rule.usernameRequired'),
  (v: string) => REGEX_USERNAME.test(v) || t('account.rule.username'),
]

// Binding names an account that already exists, so only presence is checked.
const bindUsernameRules = [(v: string) => !!v || t('account.rule.usernameRequired')]
const bindPasswordRules = [(v: string) => !!v || t('account.rule.passwordRequired')]

const nicknameRules = [
  (v: string) => !!v || t('account.rule.displayNameRequired'),
  (v: string) => /^[a-zA-Z0-9_\u4e00-\u9fa5]{1,50}$/.test(v) || t('account.rule.displayName'),
]

const emailRules = [(v: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim()) || t('account.rule.emailInvalid')]

const inviteCodeRules = [(v: string) => !!v?.trim() || t('account.enterAnInvitationCode')]

const newPasswordRules = [(v: string) => REGEX_PASSWORD.test(v) || t('account.rule.passwordInvalid')]

const confirmPasswordRules = [
  (v: string) => !!v || t('account.rule.confirmPasswordRequired'),
  (v: string) => v === createPassword.value || t('account.rule.passwordsDoNotMatch'),
]

// The form is only sent once its own rules pass; the values go up to the
// container, which runs the calls.
const submitCreate = async () => {
  if (!createFormRef.value) return
  const { valid } = await createFormRef.value.validate()
  if (!valid) return
  emit('create', {
    username: createUsername.value,
    nickname: createNickname.value,
    email: createEmail.value,
    inviteCode: createInviteCode.value,
    setPassword: setPassword.value,
    password: createPassword.value,
  })
}

const submitBind = async () => {
  if (!bindFormRef.value) return
  const { valid } = await bindFormRef.value.validate()
  if (!valid) return
  emit('bind', { username: bindUsername.value, password: bindPassword.value })
}
</script>

<style scoped>
.oauth-choice {
  display: grid;
  grid-template-columns: 1fr 1fr;
  width: 100%;
  height: 40px;
  margin-bottom: 24px;
  border-color: var(--line-2);
}

.oauth-choice :deep(.v-btn--active) {
  color: var(--ink);
  background: var(--fill-2);
}

.oauth-choice :deep(.v-btn--active .v-btn__overlay) {
  opacity: 0;
}

.oauth-set-password__label {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}
</style>
