<!--
  Where a third-party sign-in comes back to: the refresh cookie its redirect
  set is traded for a session, this device remembers the way it signed in, and
  the person goes on to wherever they were headed. What it shows is
  OAuthSuccessView.vue.
-->
<template>
  <OAuthSuccessView :processing="processing" :error="error" :provider-name="providerName" />
</template>

<script lang="ts" setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { rememberSignIn } from './lastSignIn'
import { oauthProviderName } from './oauthProvider'
import OAuthSuccessView from './OAuthSuccessView.vue'

import { t } from '@/i18n'
import { takeOAuthRedirect } from '@/router/loginRedirect'
import AccountService from '@/services/account'

const route = useRoute()
const router = useRouter()

const processing = ref(true)
const error = ref('')
const providerName = ref('OAuth')

onMounted(async () => {
  const linked = route.query.linked as string
  const provider = route.query.provider as string

  if (provider) providerName.value = oauthProviderName(provider)

  // 登录用的 state 由后端签发和核对，出站时存下的这一份用不上，清掉。
  localStorage.removeItem('oauth_state')

  // 回跳地址里不带令牌：后端只设了刷新 cookie，拿它换访问令牌。
  const outcome = await AccountService.resumeFromCookie()
  processing.value = false
  if (outcome !== 'ok') {
    error.value = outcome === 'rejected' ? t('account.oauth.success.incomplete') : t('account.oauth.success.failed')
    return
  }

  // 根据是否为绑定操作显示不同的成功消息
  toast.success(
    linked === 'true'
      ? t('account.oauth.success.linked', { provider: providerName.value })
      : t('account.oauth.success.signedIn', { provider: providerName.value })
  )

  if (linked !== 'true' && provider) rememberSignIn(`oauth:${provider}`)
  router.replace(takeOAuthRedirect())
})
</script>
