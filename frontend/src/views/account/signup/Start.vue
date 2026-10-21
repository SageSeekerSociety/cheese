<!--
  Creating an account: the registration settings say whether an invitation code
  is wanted, the agreements are fetched and confirmed, the registration goes to
  the server and the browser is sent on to verify the address. What it shows is
  StartView.vue.
-->
<template>
  <StartView
    ref="viewRef"
    :error="error"
    :require-invite-code="requireInviteCode"
    :preset-invite-code="presetInviteCode"
    :registration-config-ready="registrationConfigReady"
    :submitting="submitting"
    :consent-documents="consentDocuments"
    :consent-load-error="consentLoadError"
    @submit="submit"
  />
</template>

<script lang="ts" setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useConsentDocuments } from '@/composables/useConsentDocuments'

import { attemptMessage } from '../attemptWait'
import { inviteCodeFor } from '../inviteCode'

import StartView, { type SignUpValues } from './StartView.vue'

import { axiosErrorMessage } from '@/api/axiosHttp'
import { UserAuthApi } from '@/api/userAuth'
import { t } from '@/i18n'
import { useSignupStore } from '@/stores/signup'

const error = ref('')
const requireInviteCode = ref(false)
const registrationConfigReady = ref(false)
const submitting = ref(false)

const viewRef = ref<InstanceType<typeof StartView> | null>(null)
const { documents: consentDocuments, loadError: consentLoadError, load: loadConsentDocuments } = useConsentDocuments()
// 同意要交后端当前的协议版本；一进页面就取，提交时 `confirm()` 才有东西可交。
onMounted(() => {
  void loadConsentDocuments()
})

const signupStore = useSignupStore()
const router = useRouter()
// A friend's invite link (`?code=…`) fills the code in, and shows the field even
// when registration does not require one.
const presetInviteCode = inviteCodeFor(useRoute().query.code)

onMounted(async () => {
  try {
    const data = await UserAuthApi.getRegistrationConfig()
    requireInviteCode.value = data.requireInviteCode
    registrationConfigReady.value = true
  } catch (e) {
    error.value = axiosErrorMessage(e, t('account.registrationSettingsCouldNotBeLoadedRefresh'))
  }
})

const submit = async (value: SignUpValues) => {
  if (submitting.value) return
  error.value = ''
  await loadConsentDocuments()
  const consent = await viewRef.value?.confirmConsent()
  if (!consent) return
  submitting.value = true
  try {
    await signupStore.startSignup({
      ...value,
      inviteCode: value.inviteCode?.trim() || undefined,
      consent,
    })

    router.push('/account/signup/verify-email')
  } catch (e) {
    error.value = attemptMessage(e) ?? axiosErrorMessage(e, t('account.signUp.failed'))
  } finally {
    submitting.value = false
  }
}
</script>
