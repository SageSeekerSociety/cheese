<template>
  <div>
    <AccountHeading
      :title="t('account.twoFactor.title')"
      :lede="codeType === 'totp' ? t('account.twoFactor.totpLede') : t('account.twoFactor.backupLede')"
    />

    <v-alert v-if="errorMessage" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorMessage }}
    </v-alert>

    <v-form @submit.prevent="handleVerify">
      <v-otp-input
        v-if="codeType === 'totp'"
        v-model="totpCode"
        length="6"
        type="number"
        class="account-otp"
        @update:model-value="handleTOTPInput"
      />

      <v-otp-input
        v-else
        v-model="backupCode"
        length="8"
        type="text"
        class="account-otp"
        @update:model-value="handleBackupInput"
      />

      <p class="account-hint">{{ t('account.twoFactor.lockout') }}</p>

      <v-btn
        block
        color="primary"
        size="large"
        type="submit"
        class="account-submit"
        :loading="loading"
        :disabled="!validateCode(codeType === 'totp' ? totpCode : backupCode)"
      >
        {{ codeType === 'totp' ? t('account.twoFactor.totpSubmit') : t('account.twoFactor.backupSubmit') }}
      </v-btn>

      <div class="account-foot account-foot--split">
        <button type="button" class="account-link" @click="toggleCodeType">
          {{ codeType === 'totp' ? t('account.twoFactor.useBackup') : t('account.twoFactor.useTotp') }}
        </button>
        <router-link :to="backToSignIn()" class="account-link account-link--quiet">
          {{ t('account.backToSignIn') }}
        </router-link>
      </div>
    </v-form>

    <v-dialog v-model="showBackupCodeDialog" max-width="400" persistent>
      <v-card :title="t('account.twoFactor.backupUsedTitle')">
        <v-card-text>{{ t('account.twoFactor.backupUsedBody') }}</v-card-text>
        <v-card-actions class="justify-end">
          <v-btn variant="text" @click="handleLater">
            {{ t('account.twoFactor.later') }}
          </v-btn>
          <v-btn color="primary" variant="flat" @click="handleGoToSecurity">
            {{ t('account.twoFactor.regenerate') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { attemptMessage } from './attemptWait'
import { landingAfterSignIn, takePasswordStep, upgradeAfterPasswordSignIn } from './passkeyEnrollment'

import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { postLoginTarget, takeOAuthRedirect } from '@/router/loginRedirect'
import AccountService from '@/services/account'

const router = useRouter()

const route = useRoute()

// 密码登录把来路放在 ?redirect= 里带过来；OAuth 登录开了 2FA 时是后端直接
// 跳到这里，URL 上没有来路，用出站前存下的那一个。
function afterSignIn(): string {
  return route.query.redirect !== undefined ? postLoginTarget(route.query) : takeOAuthRedirect()
}

// 退回登录页时，来路跟着走。
const backToSignIn = () => ({ name: 'SignIn', query: { redirect: route.query.redirect } })

const codeType = ref<'totp' | 'backup'>('totp')
const totpCode = ref('')
const backupCode = ref('')
const loading = ref(false)
const errorMessage = ref('')
const showBackupCodeDialog = ref(false)

const handleVerify = async () => {
  // 填满最后一位就自动提交，再按回车会带着同一张一次性票再交一次。
  if (loading.value) return
  const code = codeType.value === 'totp' ? totpCode.value : backupCode.value

  if (!validateCode(code)) {
    errorMessage.value =
      codeType.value === 'totp' ? t('account.twoFactor.totpRequired') : t('account.twoFactor.backupRequired')
    return
  }

  const token = route.query.token as string
  if (!token) {
    router.replace(backToSignIn())
    return
  }

  loading.value = true
  errorMessage.value = ''

  try {
    const { data } = await UserApi.verify2FA({
      temp_token: token,
      code: code,
    })

    // 登录成功
    AccountService.login(data.accessToken!, data.user!)
    toast.success(t('account.signIn.signedIn'))
    const upgrade = takePasswordStep() ? upgradeAfterPasswordSignIn(data.user!.id, data.passkeyEnrollment) : null

    // 如果使用了备用码，显示提醒对话框。它已经是登录后的下一件事，不再接着
    // 提议添加通行密钥。
    if (data.usedBackupCode) {
      showBackupCodeDialog.value = true
    } else {
      router.replace(await landingAfterSignIn(upgrade, afterSignIn()))
    }
  } catch (error: any) {
    // 验证票是一次性的（#357），所以每次失败后端都会连同拒绝理由回一张新票。
    // 三种拒绝要三种处置：留在本页重试 / 回去重新登录 / 等到期限过去——压成同一句
    // 提示的话，用户会一直重试一个根本不可能成功的操作。
    const detail = error?.error?.data ?? {}
    totpCode.value = ''
    backupCode.value = ''

    if (detail.reason === 'invalid_code' && detail.tempToken) {
      // 换上新票继续留在本页。旧票已经作废，不换的话下一次必然撞上
      // “验证会话已失效”，看起来像是系统坏了。
      router.replace({ name: 'Verify2FA', query: { ...route.query, token: detail.tempToken } })
      errorMessage.value =
        typeof detail.attemptsRemaining === 'number'
          ? t('account.twoFactor.wrongCodeRemaining', { count: detail.attemptsRemaining })
          : t('account.twoFactor.wrongCode')
      return
    }

    toast.error(attemptMessage(error) ?? t('account.twoFactor.sessionExpired'))
    router.replace(backToSignIn())
  } finally {
    loading.value = false
  }
}

const validateCode = (code: string) => {
  if (codeType.value === 'totp') {
    return /^\d{6}$/.test(code)
  }
  return /^[a-zA-Z0-9]{8}$/.test(code)
}

const handleTOTPInput = (value: string) => {
  backupCode.value = ''
  if (value.length === 6) {
    handleVerify()
  }
}

const handleBackupInput = (value: string) => {
  totpCode.value = ''
  if (value.length === 8) {
    handleVerify()
  }
}

const toggleCodeType = () => {
  codeType.value = codeType.value === 'totp' ? 'backup' : 'totp'
  totpCode.value = ''
  backupCode.value = ''
  errorMessage.value = ''
}

const handleGoToSecurity = () => {
  showBackupCodeDialog.value = false
  router.push({ name: 'UserSettingsSecurity' })
}

const handleLater = () => {
  showBackupCodeDialog.value = false
  router.replace(afterSignIn())
}

onMounted(() => {
  if (!route.query.token) {
    router.replace(backToSignIn())
  }
})
</script>
