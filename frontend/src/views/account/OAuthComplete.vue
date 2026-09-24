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
        @change="step = 'form'"
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
          <v-btn value="create">{{ t('account.oauth.complete.create') }}</v-btn>
          <v-btn value="bind">{{ t('account.oauth.complete.bind') }}</v-btn>
        </v-btn-toggle>

        <transition name="account-page" mode="out-in">
          <v-form
            v-if="selectedOption === 'create'"
            key="create"
            ref="createFormRef"
            @submit.prevent="handleCreateAccount"
          >
            <AccountField :label="t('account.field.username')" input-id="oauth-create-username">
              <v-text-field
                id="oauth-create-username"
                v-model="createUsername"
                autocomplete="username"
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

            <LegalConsent ref="consentRef" :action-label="t('account.agreeAndSignUp')" class="mb-4" />

            <v-btn
              type="submit"
              block
              color="primary"
              size="large"
              class="account-submit"
              :loading="creating"
              :disabled="!registrationConfigReady"
            >
              {{ t('account.signUp.submit') }}
            </v-btn>
          </v-form>

          <v-form v-else key="bind" ref="bindFormRef" @submit.prevent="handleBindAccount">
            <AccountField :label="t('account.field.username')" input-id="oauth-bind-username">
              <v-text-field
                id="oauth-bind-username"
                v-model="bindUsername"
                autocomplete="username"
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

            <v-btn type="submit" block color="primary" size="large" class="account-submit" :loading="binding">
              {{ t('account.oauth.complete.bindSubmit') }}
            </v-btn>
          </v-form>
        </transition>
      </div>
    </transition>

    <template v-else>
      <AccountHeading :title="t('account.oauth.complete.unavailable')" :lede="error" />
      <v-btn block color="primary" size="large" :to="{ name: 'SignIn' }" class="account-submit">
        {{ t('account.backToSignIn') }}
      </v-btn>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { AcceptedDocuments, ConsentMethod } from '@/network/api/legal/types'
import type { OAuthCreateUserRequest, OAuthState } from '@/network/api/users/types'

import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { REGEX_PASSWORD, REGEX_USERNAME } from '@/utils/form'

import { emailCodeMessage } from './attemptWait'
import EmailCodeStep from './EmailCodeStep.vue'
import { oauthProviderName } from './oauthProvider'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import LegalConsent from '@/components/account/LegalConsent.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const error = ref('')
const oauthState = ref<OAuthState | null>(null)
const selectedOption = ref<'create' | 'bind'>('create')
const creating = ref(false)
const binding = ref(false)
// Proving the address is a step of creating the account: the form, then the
// code sent to the address it names.
const step = ref<'form' | 'code'>('form')
// Proving an address hands back the same token carrying it, which the address
// bar keeps so a reload does not ask for the code again.
const stateToken = ref(typeof route.query.stateToken === 'string' ? route.query.stateToken : '')

// Create form fields
const createUsername = ref('')
const createNickname = ref('')
const setPassword = ref(false)
const createPassword = ref('')
const confirmPassword = ref('')
const createInviteCode = ref('')
const createEmail = ref('')
// What the consent prompt answered on the form, sent once the address is proven.
let consentGiven: { documents: AcceptedDocuments; method: ConsentMethod } | null = null
const requireInviteCode = ref(false)
const registrationConfigReady = ref(false)

// Bind form fields
const bindUsername = ref('')
const bindPassword = ref('')

// Form refs
const createFormRef = ref()
const consentRef = ref<InstanceType<typeof LegalConsent> | null>(null)
const bindFormRef = ref()

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

const providerName = computed(() => (oauthState.value ? oauthProviderName(oauthState.value.providerId) : ''))

const handleCreateAccount = async () => {
  if (!createFormRef.value) return
  const { valid } = await createFormRef.value.validate()
  if (!valid || !oauthState.value) return
  const consent = await consentRef.value?.confirm()
  if (!consent) return
  consentGiven = consent

  error.value = ''
  const verified = oauthState.value.userInfo.verifiedEmail
  if (verified && verified.toLowerCase() === createEmail.value.trim().toLowerCase()) {
    submitCreate()
    return
  }

  creating.value = true
  try {
    await sendCode()
    step.value = 'code'
  } catch (e) {
    error.value = emailCodeMessage(e) ?? t('account.verifyEmail.resendFailed')
  } finally {
    creating.value = false
  }
}

const sendCode = () => UserApi.sendOAuthEmailCode({ stateToken: stateToken.value, email: createEmail.value.trim() })

const verifyEmail = async (code: string) => {
  const email = createEmail.value.trim()
  const { data } = await UserApi.verifyOAuthEmail({ stateToken: stateToken.value, email, code })
  if (data.ownership) {
    // The address belongs to an account already: that account is proven and
    // linked on the verify page, never merged on the address alone.
    toast.info(t('account.oauth.complete.emailBelongsToAccount'))
    await router.replace({ name: 'OAuthVerify', query: { ...data.ownership } })
    return
  }
  stateToken.value = data.stateToken
  if (oauthState.value) oauthState.value.userInfo.verifiedEmail = email
  await router.replace({ query: { ...route.query, stateToken: data.stateToken } })
  submitCreate()
}

// The form post is answered with a redirect to the success or error page.
const submitCreate = () => {
  if (!consentGiven) return
  creating.value = true
  const requestData: OAuthCreateUserRequest = {
    stateToken: stateToken.value,
    username: createUsername.value,
    nickname: createNickname.value,
    passwordMode: setPassword.value ? 'password' : 'none',
    consentTerms: consentGiven.documents.terms,
    consentPrivacy: consentGiven.documents.privacy,
    consentMethod: consentGiven.method,
  }
  if (requireInviteCode.value) {
    requestData.inviteCode = createInviteCode.value.trim()
  }
  if (setPassword.value) {
    requestData.password = createPassword.value
  }
  UserApi.createUserFromOAuth(requestData)
}

const handleBindAccount = async () => {
  if (!bindFormRef.value) return
  const { valid } = await bindFormRef.value.validate()
  if (!valid || !oauthState.value) return

  binding.value = true
  error.value = ''

  try {
    UserApi.bindOAuthToUser({
      stateToken: stateToken.value,
      username: bindUsername.value,
      password: bindPassword.value,
    })
  } catch {
    error.value = t('account.oauth.error.bindingFailed')
    binding.value = false
  }
}

const loadOAuthState = async () => {
  if (!stateToken.value) {
    error.value = t('account.oauth.error.sessionExpired')
    loading.value = false
    return
  }

  try {
    const response = await UserApi.getOAuthState(stateToken.value)
    oauthState.value = response.data

    // Pre-fill form fields with suggested values
    createUsername.value = response.data.suggestedUsername
    createNickname.value = response.data.suggestedNickname
    createEmail.value = response.data.userInfo.verifiedEmail ?? response.data.userInfo.email ?? ''
  } catch (err) {
    error.value = requestErrorMessage(err, t('account.oauth.error.sessionExpired'))
  } finally {
    loading.value = false
  }
}

const loadRegistrationConfig = async () => {
  try {
    const { data } = await UserApi.getRegistrationConfig()
    requireInviteCode.value = data.requireInviteCode
    registrationConfigReady.value = true
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.registrationSettingsCouldNotBeLoadedRefresh'))
  }
}

onMounted(() => {
  loadOAuthState()
  loadRegistrationConfig()
})
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
