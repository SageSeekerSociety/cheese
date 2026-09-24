<template>
  <div>
    <template v-if="error">
      <AccountHeading :title="t('account.oauth.error.title')" />
      <v-alert type="error" variant="tonal" density="comfortable" class="mb-6">
        {{ error }}
      </v-alert>
      <v-btn block color="primary" size="large" to="/account/signin" class="account-submit">
        {{ t('account.backToSignIn') }}
      </v-btn>
    </template>

    <template v-else>
      <AccountHeading
        :title="processing ? t('account.oauth.success.processing') : t('account.oauth.success.done')"
        :lede="
          processing
            ? t('account.oauth.success.processingLede', { provider: providerName })
            : t('account.oauth.success.doneLede')
        "
      />
      <v-progress-linear indeterminate color="primary" height="2" />
    </template>
  </div>
</template>

<script lang="ts" setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { rememberSignIn } from './lastSignIn'
import { oauthProviderName } from './oauthProvider'

import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'
import { takeOAuthRedirect } from '@/router/loginRedirect'
import AccountService from '@/services/account'

const route = useRoute()
const router = useRouter()

const processing = ref(true)
const error = ref('')
const providerName = ref('OAuth')

onMounted(async () => {
  const token = route.query.token as string
  const linked = route.query.linked as string
  const provider = route.query.provider as string

  if (provider) providerName.value = oauthProviderName(provider)

  if (!token) {
    processing.value = false
    error.value = t('account.oauth.success.missingToken')
    return
  }

  try {
    // 登录用的 state 由后端签发和核对，出站时存下的这一份用不上，清掉。
    localStorage.removeItem('oauth_state')

    // 直接使用 token 登录，login 方法会自动获取完整的用户信息
    await AccountService.login(token)

    processing.value = false

    // 根据是否为绑定操作显示不同的成功消息
    toast.success(
      linked === 'true'
        ? t('account.oauth.success.linked', { provider: providerName.value })
        : t('account.oauth.success.signedIn', { provider: providerName.value })
    )

    if (linked !== 'true' && provider) rememberSignIn(`oauth:${provider}`)
    router.replace(takeOAuthRedirect())
  } catch (err) {
    processing.value = false
    error.value = t('account.oauth.success.failed')
    console.error('OAuth 登录成功处理失败:', err)
  }
})
</script>
