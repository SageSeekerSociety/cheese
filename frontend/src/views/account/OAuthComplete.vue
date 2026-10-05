<!--
  Third-party sign-up: reads the state token from the address, loads the
  provider's returned identity and the registration settings, sends the mailed
  code, and creates or binds the account. What it shows is OAuthCompleteView.vue.
-->
<template>
  <OAuthCompleteView
    ref="viewRef"
    :loading="loading"
    :error="error"
    :oauth-state="oauthState"
    :provider-name="providerName"
    :step="step"
    :require-invite-code="requireInviteCode"
    :registration-config-ready="registrationConfigReady"
    :creating="creating"
    :binding="binding"
    :consent-documents="consentDocuments"
    :consent-load-error="consentLoadError"
    :verify-email="verifyEmail"
    :send-code="sendCode"
    @create="handleCreateAccount"
    @bind="handleBindAccount"
    @change="step = 'form'"
  />
</template>

<script setup lang="ts">
import type { AcceptedDocuments, ConsentMethod } from '@/network/api/legal/types'
import type { OAuthCreateUserRequest, OAuthState } from '@/network/api/users/types'
import type { OAuthBindValues, OAuthCreateValues } from './OAuthCompleteView.vue'

import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { useConsentDocuments } from '@/composables/useConsentDocuments'

import { emailCodeMessage } from './attemptWait'
import OAuthCompleteView from './OAuthCompleteView.vue'
import { oauthProviderName } from './oauthProvider'

import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'

const route = useRoute()
const router = useRouter()

const viewRef = ref<InstanceType<typeof OAuthCompleteView> | null>(null)
const loading = ref(true)
const error = ref('')
const oauthState = ref<OAuthState | null>(null)
const creating = ref(false)
const binding = ref(false)
// Proving the address is a step of creating the account: the form, then the
// code sent to the address it names.
const step = ref<'form' | 'code'>('form')
// Proving an address hands back the same token carrying it, which the address
// bar keeps so a reload does not ask for the code again.
const stateToken = ref(typeof route.query.stateToken === 'string' ? route.query.stateToken : '')

// What the create form was sent with, kept until the address is proven.
let createValues: OAuthCreateValues | null = null
// What the consent prompt answered on the form, sent once the address is proven.
let consentGiven: { documents: AcceptedDocuments; method: ConsentMethod } | null = null
const requireInviteCode = ref(false)
const registrationConfigReady = ref(false)

const { documents: consentDocuments, loadError: consentLoadError, load: loadConsentDocuments } = useConsentDocuments()
// 同意要交后端当前的协议版本；一进页面就取，提交时 `confirm()` 才有东西可交。
onMounted(() => {
  void loadConsentDocuments()
})

const providerName = computed(() => (oauthState.value ? oauthProviderName(oauthState.value.providerId) : ''))

const handleCreateAccount = async (values: OAuthCreateValues) => {
  if (!oauthState.value) return
  await loadConsentDocuments()
  const consent = await viewRef.value?.confirmConsent()
  if (!consent) return
  consentGiven = consent
  createValues = values

  error.value = ''
  const verified = oauthState.value.userInfo.verifiedEmail
  if (verified && verified.toLowerCase() === values.email.trim().toLowerCase()) {
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

const sendCode = () =>
  UserApi.sendOAuthEmailCode({ stateToken: stateToken.value, email: createValues?.email.trim() ?? '' })

const verifyEmail = async (code: string) => {
  const email = createValues?.email.trim() ?? ''
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
  if (!consentGiven || !createValues) return
  creating.value = true
  const requestData: OAuthCreateUserRequest = {
    stateToken: stateToken.value,
    username: createValues.username,
    nickname: createValues.nickname,
    passwordMode: createValues.setPassword ? 'password' : 'none',
    consentTerms: consentGiven.documents.terms,
    consentPrivacy: consentGiven.documents.privacy,
    consentMethod: consentGiven.method,
  }
  if (requireInviteCode.value) {
    requestData.inviteCode = createValues.inviteCode.trim()
  }
  if (createValues.setPassword) {
    requestData.password = createValues.password
  }
  UserApi.createUserFromOAuth(requestData)
}

const handleBindAccount = async (values: OAuthBindValues) => {
  binding.value = true
  error.value = ''

  try {
    UserApi.bindOAuthToUser({
      stateToken: stateToken.value,
      username: values.username,
      password: values.password,
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
