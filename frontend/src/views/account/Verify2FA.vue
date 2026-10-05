<!--
  The second-factor step: it reads where the sign-in came from, sends the code
  to the backend, decides where to land next and runs the passkey upgrade.
  What it shows is Verify2FAView.vue, which is handed the code state.
-->
<template>
  <Verify2FAView
    :code-type="codeType"
    :error-message="errorMessage"
    :loading="loading"
    :submit-disabled="submitDisabled"
    :trust-device="trustDevice"
    :totp-code="totpCode"
    :backup-code="backupCode"
    :back-to="backTo"
    :show-backup-code-dialog="showBackupCodeDialog"
    @submit="handleVerify"
    @toggle="toggleCodeType"
    @confirm="handleGoToSecurity"
    @cancel="handleLater"
    @update:trust-device="trustDevice = $event"
    @update:totp-code="handleTOTPInput"
    @update:backup-code="handleBackupInput"
    @update:show-backup-code-dialog="showBackupCodeDialog = $event"
  />
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { attemptMessage } from './attemptWait'
import { landingAfterSignIn, takeFirstStep, upgradeAfterSecondStep } from './passkeyEnrollment'
import Verify2FAView from './Verify2FAView.vue'

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
const backTo = computed(() => backToSignIn())

const codeType = ref<'totp' | 'backup'>('totp')
// 默认不勾：在公用电脑上勾选，之后任何人只凭密码就能登录这个账号。
const trustDevice = ref(false)
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
      trust_device: trustDevice.value,
    })

    // 登录成功
    AccountService.login(data.accessToken!, data.user!)
    toast.success(t('account.signIn.signedIn'))
    const upgrade = upgradeAfterSecondStep(takeFirstStep(), data.user!.id, data.passkeyEnrollment)

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

const submitDisabled = computed(() => !validateCode(codeType.value === 'totp' ? totpCode.value : backupCode.value))

const handleTOTPInput = (value: string) => {
  totpCode.value = value
  backupCode.value = ''
  if (value.length === 6) {
    handleVerify()
  }
}

const handleBackupInput = (value: string) => {
  backupCode.value = value
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
