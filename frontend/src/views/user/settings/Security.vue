<template>
  <v-sheet rounded="lg" class="bg-transparent">
    <v-card class="mb-4" rounded="lg" variant="flat">
      <v-card-item>
        <v-card-title class="text-h6 font-weight-medium">
          <v-icon icon="mdi-shield-account" class="mr-2"></v-icon>
          {{ t('users.settings.security.title') }}
        </v-card-title>
      </v-card-item>

      <v-divider></v-divider>

      <v-list class="py-0" lines="two" density="comfortable">
        <v-list-item>
          <template #prepend>
            <v-avatar color="surface-variant" variant="tonal" rounded="lg" size="48">
              <v-icon icon="mdi-lock-reset"></v-icon>
            </v-avatar>
          </template>

          <v-list-item-title class="font-weight-medium">{{
            t('users.settings.security.loginPassword')
          }}</v-list-item-title>
          <v-list-item-subtitle class="text-caption">{{
            t('users.settings.security.lastUpdated', { date: formatDate(passwordLastUpdated) })
          }}</v-list-item-subtitle>

          <template #append>
            <v-btn
              variant="tonal"
              color="primary"
              prepend-icon="mdi-key-change"
              @click="showChangePasswordDialog = true"
            >
              {{ t('users.settings.security.changePassword') }}
            </v-btn>
          </template>
        </v-list-item>
      </v-list>
    </v-card>

    <v-card variant="flat" rounded="lg" class="mb-4">
      <v-card-item>
        <v-card-title class="text-h6 font-weight-medium">
          <v-icon icon="mdi-key-chain" class="mr-2"></v-icon>
          {{ t('users.settings.security.passkeys') }}
          <v-chip v-if="!webAuthnSupported" label size="small" color="warning" class="ml-2">
            {{ t('users.settings.security.browserUnsupported') }}
          </v-chip>
        </v-card-title>
      </v-card-item>

      <v-divider></v-divider>

      <v-card-text>
        <div class="d-flex flex-column">
          <transition-group name="fade" tag="div" class="d-flex flex-column">
            <v-list-item v-for="passkey in passkeys" :key="passkey.id" class="px-0">
              <template #prepend>
                <v-avatar color="surface-variant" variant="tonal" size="40" class="mr-3">
                  <v-icon :icon="getDeviceIcon(passkey.deviceType)" size="20"></v-icon>
                </v-avatar>
              </template>

              <v-list-item-title class="font-weight-medium">
                {{ getPasskeyTitle(passkey) || t('users.settings.security.unnamedPasskey') }}
                <span class="text-caption text-medium-emphasis ml-2">
                  {{ t('users.settings.security.addedOn', { date: formatDate(passkey.createdAt) }) }}
                </span>
              </v-list-item-title>

              <template #append>
                <v-btn
                  icon="mdi-delete-outline"
                  variant="text"
                  color="error"
                  size="small"
                  class="ml-2"
                  @click="handleDeletePasskey(passkey.id)"
                ></v-btn>
              </template>
            </v-list-item>
          </transition-group>

          <v-alert v-if="passkeys.length === 0" variant="text" color="info" icon="mdi-information-outline" class="my-2">
            {{ t('users.settings.security.noPasskeys') }}
          </v-alert>

          <div class="d-flex justify-end mt-4">
            <v-btn
              variant="tonal"
              prepend-icon="mdi-key-plus"
              :loading="addingPasskey"
              :disabled="!webAuthnSupported"
              @click="handleAddPasskey"
            >
              {{ t('users.settings.security.addPasskey') }}
            </v-btn>
          </div>
        </div>
      </v-card-text>
    </v-card>

    <v-card variant="flat" rounded="lg" class="mb-4">
      <v-card-item>
        <v-card-title class="text-h6 font-weight-medium">
          <v-icon icon="mdi-two-factor-authentication" class="mr-2"></v-icon>
          {{ t('users.settings.security.twoFactor') }}
          <v-chip :color="totp2FAEnabled ? 'success' : 'surface-variant'" label size="small" class="ml-2">
            {{ totp2FAEnabled ? t('users.settings.security.enabled') : t('users.settings.security.inactive') }}
          </v-chip>
        </v-card-title>
      </v-card-item>

      <v-divider></v-divider>

      <v-card-text>
        <v-alert variant="tonal" :color="totp2FAEnabled ? 'success' : 'info'" class="mb-4" rounded="lg">
          <template #prepend>
            <v-icon :icon="totp2FAEnabled ? 'mdi-shield-check' : 'mdi-shield-alert'"></v-icon>
          </template>
          {{ t('users.settings.security.securityLevel', { level: securityLevel }) }}
        </v-alert>

        <div class="d-flex flex-column gap-4">
          <v-card v-if="totp2FAEnabled" variant="flat" class="inner-card" rounded="lg">
            <v-list lines="two">
              <!-- <v-list-item>
                <template #prepend>
                  <v-icon icon="mdi-cellphone-key" color="primary"></v-icon>
                </template>
                <v-list-item-title>验证器应用</v-list-item-title>
              </v-list-item> -->

              <v-list-item>
                <template #prepend>
                  <v-icon icon="mdi-backup-restore" color="primary"></v-icon>
                </template>
                <v-list-item-title>{{ t('users.settings.security.backupCodes') }}</v-list-item-title>
                <template #append>
                  <v-btn variant="tonal" color="primary" @click="handleGenerateBackupCodes">
                    {{ t('users.settings.security.generateCodes') }}
                  </v-btn>
                </template>
              </v-list-item>
            </v-list>
          </v-card>

          <div class="d-flex justify-end">
            <v-btn
              :color="totp2FAEnabled ? 'error' : 'primary'"
              variant="tonal"
              class="text-capitalize"
              @click="totp2FAEnabled ? (showDisableDialog = true) : handleInitTOTP()"
            >
              {{ totp2FAEnabled ? t('users.settings.security.disable2fa') : t('users.settings.security.enable2fa') }}
            </v-btn>
          </div>
        </div>
      </v-card-text>
    </v-card>

    <v-dialog v-model="showChangePasswordDialog" max-width="500" scrollable persistent>
      <v-card style="max-height: 80vh">
        <v-card-text class="overflow-y-auto">
          <v-toolbar density="compact" color="surface">
            <v-toolbar-title class="text-h6">{{ t('users.settings.security.changePasswordTitle') }}</v-toolbar-title>
            <v-btn icon="mdi-close" @click="handleCancelChangePassword" />
          </v-toolbar>

          <v-form ref="passwordForm" @submit.prevent="handleChangePassword">
            <v-text-field
              id="field-newPassword"
              v-model="newPassword"
              autocomplete="new-password"
              name="newPassword"
              :label="t('users.settings.security.newPassword')"
              type="password"
              variant="outlined"
              :rules="[
                (v) => !!v || t('users.settings.security.newPasswordRequired'),
                (v) => (v && v.length >= 8) || t('users.settings.security.newPasswordMin'),
                (v) => REGEX_PASSWORD.test(v) || t('users.settings.security.newPasswordCharset'),
              ]"
            >
            </v-text-field>

            <v-text-field
              id="field-confirmPassword"
              v-model="confirmPassword"
              autocomplete="new-password"
              name="confirmPassword"
              :label="t('users.settings.security.confirmPassword')"
              type="password"
              variant="outlined"
              :rules="[
                (v) => !!v || t('users.settings.security.confirmRequired'),
                (v) => v === newPassword || t('users.settings.security.confirmMismatch'),
              ]"
            ></v-text-field>

            <div class="d-flex justify-end gap-2 mt-6">
              <v-btn variant="text" @click="handleCancelChangePassword">{{ t('global.cancel') }}</v-btn>
              <v-btn color="primary" type="submit">{{ t('users.settings.security.confirmChange') }}</v-btn>
            </div>
          </v-form>
        </v-card-text>
      </v-card>
    </v-dialog>

    <v-dialog v-model="showTOTPSetup" fullscreen :scrim="false" transition="dialog-bottom-transition">
      <v-card class="d-flex flex-column" color="surface">
        <v-toolbar density="compact">
          <v-btn icon="mdi-close" @click="handleCancelSetup" />
          <v-toolbar-title>{{ t('users.settings.security.setupTitle') }}</v-toolbar-title>
          <v-spacer />
          <v-progress-circular :model-value="setupProgress" :size="32" color="primary" class="me-2">
            {{ setupStepIndex + 1 }}/3
          </v-progress-circular>
        </v-toolbar>

        <v-stepper
          v-model="setupStep"
          class="elevation-0"
          :items="[
            { title: t('users.settings.security.stepQr'), value: 'qr' },
            { title: t('users.settings.security.stepVerify'), value: 'verify' },
            { title: t('users.settings.security.stepBackup'), value: 'backup' },
          ]"
          show-actions
        >
          <v-stepper-window>
            <v-stepper-window-item value="qr">
              <div class="d-flex flex-column align-center pa-8">
                <div class="text-h5 mb-2">{{ t('users.settings.security.setupAuthenticator') }}</div>
                <div class="text-body-1 text-medium-emphasis text-center mb-8">
                  {{ t('users.settings.security.scanHint') }}
                </div>

                <v-card variant="outlined" rounded="xl" class="pa-4 mb-6">
                  <v-img :src="qrCodeData" width="240" height="240" />
                </v-card>

                <div class="text-body-1 font-weight-medium mb-2">{{ t('users.settings.security.manualKey') }}</div>
                <div class="d-flex align-center gap-2 mb-8">
                  <code class="text-h6">{{ totpSecret }}</code>
                  <v-btn icon="mdi-content-copy" size="small" variant="text" @click="copyToClipboard(totpSecret)" />
                </div>
              </div>
            </v-stepper-window-item>

            <v-stepper-window-item value="verify">
              <div class="d-flex flex-column align-center pa-8">
                <div class="text-h5 mb-2">{{ t('users.settings.security.verifyCode') }}</div>
                <div class="text-body-1 text-medium-emphasis text-center mb-8">
                  {{ t('users.settings.security.verifyCodeHint') }}
                </div>

                <v-otp-input
                  v-model="verificationCode"
                  length="6"
                  type="number"
                  variant="outlined"
                  class="mb-8"
                  :error-messages="errorMessage"
                />
              </div>
            </v-stepper-window-item>

            <v-stepper-window-item value="backup">
              <div class="d-flex flex-column align-center pa-8">
                <div class="text-h5 mb-2">{{ t('users.settings.security.backupTitle') }}</div>
                <div class="text-body-1 text-medium-emphasis text-center mb-8">
                  {{ t('users.settings.security.backupHint') }}
                </div>

                <v-card variant="outlined" rounded="lg" class="w-100 pa-4 mb-8">
                  <div class="d-flex flex-wrap gap-4 justify-center">
                    <div v-for="(code, index) in backupCodes" :key="index" class="text-body-1 font-weight-medium">
                      {{ code }}
                    </div>
                  </div>
                </v-card>
              </div>
            </v-stepper-window-item>
          </v-stepper-window>

          <template #actions="{ prev, next }">
            <v-card-actions class="justify-end gap-2">
              <v-btn v-if="setupStep !== 'qr'" variant="outlined" @click="prev">
                {{ t('users.settings.security.previousStep') }}
              </v-btn>
              <v-btn
                color="primary"
                :disabled="setupStep === 'verify' && verificationCode.length !== 6"
                @click="handleStepAction(next)"
              >
                {{
                  setupStep === 'backup' ? t('users.settings.security.finish') : t('users.settings.security.nextStep')
                }}
              </v-btn>
            </v-card-actions>
          </template>
        </v-stepper>
      </v-card>
    </v-dialog>

    <v-dialog v-model="showDisableDialog" max-width="500">
      <v-card>
        <v-toolbar density="compact" color="surface">
          <v-toolbar-title class="text-h6">{{ t('users.settings.security.disableTitle') }}</v-toolbar-title>
          <v-btn icon="mdi-close" @click="showDisableDialog = false" />
        </v-toolbar>

        <v-card-text class="pa-4">
          <div class="text-body-1 mb-4">{{ t('users.settings.security.disableConfirm') }}</div>
          <div class="d-flex justify-end gap-2">
            <v-btn variant="text" @click="showDisableDialog = false">{{ t('global.cancel') }}</v-btn>
            <v-btn color="error" @click="handleDisableTOTP">
              {{ t('users.settings.security.disableConfirmButton') }}
            </v-btn>
          </div>
        </v-card-text>
      </v-card>
    </v-dialog>
  </v-sheet>
</template>

<script setup lang="ts">
import type { PasskeyInfo } from '@/network/api/users/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { browserSupportsWebAuthn, startRegistration } from '@simplewebauthn/browser'
import * as srp from 'secure-remote-password/client'

import { REGEX_PASSWORD } from '@/utils/form'
import { withSudo } from '@/utils/sudo'

import { UserApi } from '@/network/api/users'
import { useDialog } from '@/plugins/dialog'
import { currentUserId, currentUserName } from '@/services/account'
import { useSudoStore } from '@/stores/sudo'

const { t, locale } = useI18n()
const router = useRouter()
const dialogs = useDialog()
const loading = ref(false)
const passkeys = ref<PasskeyInfo[]>([])
const webAuthnSupported = ref(false)
const errorDialog = ref(false)
const errorMessage = ref('')

// TOTP 相关状态
const totp2FAEnabled = ref(false)
const showTOTPSetup = ref(false)
const setupStep = ref('qr')
const totpSecret = ref('')
const qrCodeData = ref('')
const verificationCode = ref('')
const backupCodes = ref<string[]>([])
const showDisableDialog = ref(false)
const showGenerateBackupCodesDialog = ref(false)

// 添加设置相关的状态
const alwaysRequired = ref(false)
const settingsLoading = ref(false)

// 修改密码相关的状态
const showChangePasswordDialog = ref(false)
const newPassword = ref('')
const confirmPassword = ref('')
const passwordError = ref('')
const isChangingPassword = ref(false)
const passwordForm = ref<any>(null)

const setupSteps = ['qr', 'verify', 'backup']
const setupStepIndex = computed(() => setupSteps.indexOf(setupStep.value))
const setupProgress = computed(() => ((setupStepIndex.value + 1) / setupSteps.length) * 100)

// 密码相关状态
const passwordLastUpdated = ref(new Date()) // 从API获取实际值

// 安全等级计算属性
const securityLevel = computed(() => {
  if (totp2FAEnabled.value && passkeys.value.length > 0) return t('users.settings.security.levelVeryHigh')
  if (totp2FAEnabled.value) return t('users.settings.security.levelHigh')
  if (passkeys.value.length > 0) return t('users.settings.security.levelMedium')
  return t('users.settings.security.levelLow')
})

// 添加状态管理
const addingPasskey = ref(false)
const deletingPasskey = ref(false)

// 获取 Passkey 列表
const fetchPasskeys = async () => {
  if (!currentUserId.value) return

  loading.value = true
  try {
    const response = await UserApi.getUserPasskeys(currentUserId.value)
    passkeys.value = response.data.passkeys
  } catch (error) {
    console.error('Failed to fetch passkeys:', error)
  } finally {
    loading.value = false
  }
}

// 格式化设备类型显示
const getPasskeyTitle = (passkey: PasskeyInfo) => {
  const deviceName =
    passkey.deviceType === 'platform'
      ? t('users.settings.security.devicePlatform')
      : t('users.settings.security.deviceSecurityKey')
  const backupStatus = passkey.backedUp
    ? t('users.settings.security.backedUp')
    : t('users.settings.security.notBackedUp')
  return `${deviceName} ${backupStatus}`
}

// 获取设备图标
const getDeviceIcon = (deviceType: string) => {
  return deviceType === 'platform' ? 'mdi-laptop' : 'mdi-key-variant'
}

// 格式化日期。跟着界面语言走：写死 'zh-CN' 的话，切到英文这一页仍会打印
// 「2026年9月17日」——日期也是要翻的。
const formatDate = (date: Date) => {
  return new Date(date).toLocaleDateString(locale.value === 'en' ? 'en' : 'zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

const showError = (message: string) => {
  toast.error(message)
  errorMessage.value = message
  errorDialog.value = true
}

const handleAddPasskey = async () => {
  if (!currentUserId.value) {
    showError(t('users.settings.security.notLoggedIn'))
    return
  }

  addingPasskey.value = true
  try {
    await withSudo(
      async () => {
        // 1. 获取注册选项
        const optionsResponse = await UserApi.getPasskeyRegistrationOptions(currentUserId.value!)
        const optionsJSON = optionsResponse.data.options

        // 2. 开始注册流程
        const attResp = await startRegistration({ optionsJSON })

        // 3. 验证注册结果
        await UserApi.verifyPasskeyRegistration(currentUserId.value!, attResp)

        // 4. 刷新列表
        await fetchPasskeys()
      },
      'addPasskey',
      null,
      router
    )
  } catch (error: any) {
    console.error('Failed to add passkey:', error)

    // 处理特定错误
    if (error.name === 'InvalidStateError') {
      showError(t('users.settings.security.passkeyAlreadyRegistered'))
    } else if (error.name === 'NotAllowedError') {
      showError(t('users.settings.security.cancelled'))
    } else {
      showError(error.message || t('users.settings.security.addPasskeyFailed'))
    }
  } finally {
    addingPasskey.value = false
  }
}

// 删除 Passkey
const handleDeletePasskey = async (credentialId: string) => {
  if (!currentUserId.value) return

  const confirmed = await dialogs
    .confirm(t('users.settings.security.deletePasskeyConfirm'), {
      title: t('users.settings.security.deletePasskeyTitle'),
    })
    .wait()

  if (!confirmed) return

  deletingPasskey.value = true
  try {
    await withSudo(
      async () => {
        await UserApi.deletePasskey(currentUserId.value!, credentialId)
        await fetchPasskeys()
        toast.success(t('users.settings.security.passkeyDeleted'))
      },
      'deletePasskey',
      null,
      router
    )
  } catch (error: any) {
    showError(error.message || t('users.settings.security.deletePasskeyFailed'))
  } finally {
    deletingPasskey.value = false
  }
}

// 获取 2FA 状态
const fetch2FAStatus = async () => {
  if (!currentUserId.value) return

  loading.value = true
  try {
    const response = await UserApi.get2FAStatus(currentUserId.value)
    totp2FAEnabled.value = response.data.enabled
    alwaysRequired.value = response.data.always_required
  } catch (error: any) {
    console.error('Failed to fetch 2FA status:', error)
    showError(error.message || t('users.settings.security.loadStatusFailed'))
  } finally {
    loading.value = false
  }
}

// 修改 onMounted
onMounted(async () => {
  webAuthnSupported.value = browserSupportsWebAuthn()
  if (webAuthnSupported.value) {
    fetchPasskeys()
  }
  fetch2FAStatus()

  // 检查 sudo store 中是否存在重试操作
  const sudoStore = useSudoStore()
  if (sudoStore.retryOperation && sudoStore.isVerified) {
    // 不打印 retryOperation：changePassword 的 opData 里装着用户刚输入的新密码，
    // 打出来就等于把明文密码留在浏览器控制台里。
    const opKey = sudoStore.retryOperation.opKey
    const retryOperations: Record<string, () => void | Promise<void>> = {
      disableTOTP: handleDisableTOTP,
      generateBackupCodes: handleConfirmGenerateBackupCodes,
      addPasskey: handleAddPasskey,
      initTOTP: handleInitTOTP,
      update2FASettings: async () => {
        if (sudoStore.retryOperation?.opData !== undefined) {
          await handleUpdateSettings(sudoStore.retryOperation.opData)
          alwaysRequired.value = sudoStore.retryOperation?.opData
        }
      },
      changePassword: async () => {
        if (sudoStore.retryOperation?.opData?.newPassword) {
          // 直接使用保存的新密码信息进行修改
          if (!currentUserId.value || !currentUserName.value) return

          const newPwd = sudoStore.retryOperation.opData.newPassword
          const srpSalt = srp.generateSalt()
          const privateKey = srp.derivePrivateKey(srpSalt, currentUserName.value, newPwd)
          const srpVerifier = srp.deriveVerifier(privateKey)

          try {
            await UserApi.changePassword(currentUserId.value, {
              srpSalt,
              srpVerifier,
            })
            toast.success(t('users.settings.security.passwordChanged'))
          } catch (error: any) {
            console.error('Failed to change password:', error)
            toast.error(error.message || t('users.settings.security.passwordChangeFailed'))
          }
        }
      },
    }
    // 等这次重试真的跑完再清。清除会把票和 opData 一起抹掉，而重试是在
    // 第一个 await 之后才去读它们的——不等，就是让操作去读自己刚被清掉的输入。
    if (retryOperations[opKey]) {
      await retryOperations[opKey]()
    }
    sudoStore.clearRetryState()
  }
})

// 初始化 TOTP
const handleInitTOTP = async () => {
  if (!currentUserId.value) return

  loading.value = true
  try {
    await withSudo(
      async () => {
        const response = await UserApi.initializeTOTP(currentUserId.value!)
        totpSecret.value = response.data.secret
        qrCodeData.value = response.data.qrcode
        showTOTPSetup.value = true
        setupStep.value = 'qr'
      },
      'initTOTP',
      null,
      router
    )
  } catch (error: any) {
    showError(error.message || t('users.settings.security.initFailed'))
  } finally {
    loading.value = false
  }
}

// 修改 handleEnableTOTP 成功后刷新状态
const handleEnableTOTP = async () => {
  if (!currentUserId.value || !verificationCode.value || !totpSecret.value) return

  loading.value = true
  try {
    const response = await UserApi.enableTOTP(currentUserId.value, {
      code: verificationCode.value,
      secret: totpSecret.value,
    })
    backupCodes.value = response.data.backup_codes
    await fetch2FAStatus()
    return true // 返回成功状态
  } catch (error: any) {
    showError(error.message || t('users.settings.security.verifyFailed'))
    return false // 返回失败状态
  } finally {
    loading.value = false
  }
}

// 修复步骤控制逻辑
// eslint-disable-next-line @typescript-eslint/no-unsafe-function-type
const handleStepAction = async (next: Function) => {
  if (setupStep.value === 'verify') {
    const success = await handleEnableTOTP()
    if (success) {
      next()
    }
  } else if (setupStep.value === 'backup') {
    handleFinishSetup()
  } else {
    next()
  }
}

// 完成设置
const handleFinishSetup = () => {
  showTOTPSetup.value = false
  setupStep.value = 'qr'
  verificationCode.value = ''
  backupCodes.value = []
}

// 修改 handleDisableTOTP 成功后刷新状态
const handleDisableTOTP = async () => {
  if (!currentUserId.value) return

  loading.value = true
  try {
    await withSudo(
      async (sudoTicket) => {
        await UserApi.disableTOTP(currentUserId.value!, sudoTicket)
        await fetch2FAStatus() // 确保状态更新
        showDisableDialog.value = false
        toast.success(t('users.settings.security.disabledNotice'))
      },
      'disableTOTP',
      null,
      router
    )
  } catch (error: any) {
    showError(error.message || t('users.settings.security.disableFailed'))
    showDisableDialog.value = false // 关闭对话框
  } finally {
    loading.value = false
  }
}

// 生成新的备份码
const handleGenerateBackupCodes = async () => {
  const confirmed = await dialogs
    .confirm(t('users.settings.security.generateCodesConfirm'), {
      title: t('users.settings.security.generateCodesTitle'),
    })
    .wait()
  if (confirmed) handleConfirmGenerateBackupCodes()
}

// 确认生成新的备份码
const handleConfirmGenerateBackupCodes = async () => {
  if (!currentUserId.value) return

  loading.value = true
  try {
    await withSudo(
      async () => {
        const response = await UserApi.generateBackupCodes(currentUserId.value!)
        backupCodes.value = response.data.backup_codes
        showGenerateBackupCodesDialog.value = false
        setupStep.value = 'backup'
        showTOTPSetup.value = true
      },
      'generateBackupCodes',
      null,
      router
    )
  } catch (error: any) {
    showError(error.message || t('users.settings.security.generateCodesFailed'))
  } finally {
    loading.value = false
  }
}

// 添加更新设置的处理函数
const handleUpdateSettings = async (value: boolean) => {
  if (!currentUserId.value) return

  settingsLoading.value = true
  try {
    await withSudo(
      async () => {
        await UserApi.update2FASettings(currentUserId.value!, value)
      },
      'update2FASettings',
      value,
      router
    )
  } catch (error: any) {
    showError(error.message || t('users.settings.security.updateSettingsFailed'))
    // 如果更新失败，恢复原来的值
    alwaysRequired.value = !value
  } finally {
    settingsLoading.value = false
  }
}

// 新增取消设置处理
const handleCancelSetup = () => {
  showTOTPSetup.value = false
  setupStep.value = 'qr'
  verificationCode.value = ''
  backupCodes.value = []
}

// 复制到剪贴板
const copyToClipboard = (text: string) => {
  navigator.clipboard.writeText(text)
  toast.success(t('users.settings.security.copied'))
}

// 处理修改密码
const handleChangePassword = async () => {
  const { valid } = await passwordForm.value.validate()
  if (!valid) return

  isChangingPassword.value = true
  try {
    await withSudo(
      async () => {
        if (!currentUserId.value || !currentUserName.value) return

        // 生成新的 SRP 盐值和验证器
        const srpSalt = srp.generateSalt()
        const privateKey = srp.derivePrivateKey(srpSalt, currentUserName.value, newPassword.value)
        const srpVerifier = srp.deriveVerifier(privateKey)

        await UserApi.changePassword(currentUserId.value, {
          srpSalt,
          srpVerifier,
        })

        toast.success(t('users.settings.security.passwordChanged'))
        handleCancelChangePassword()
      },
      'changePassword',
      { newPassword: newPassword.value }, // 将新密码信息保存在 data 中
      router
    )
  } catch (error: any) {
    console.error('Failed to change password:', error)
    passwordError.value = error.message || t('users.settings.security.passwordChangeFailed')
  } finally {
    isChangingPassword.value = false
  }
}

// 取消修改密码
const handleCancelChangePassword = () => {
  showChangePasswordDialog.value = false
  newPassword.value = ''
  confirmPassword.value = ''
  passwordError.value = ''
  if (passwordForm.value) {
    passwordForm.value.reset()
  }
}
</script>

<style scoped>
.v-list-item:hover {
  background: rgba(var(--v-theme-primary), 0.05);
  transition: background 0.2s ease;
}

.v-otp-input :deep(.v-field) {
  width: 3.5rem;
  height: 3.5rem;
  font-size: 1.5rem;
}

.v-card.inner-card {
  box-shadow: var(--shadow-1);
}

.v-list-item {
  padding: 12px 16px;
}

.list-enter-active,
.list-leave-active {
  transition: all 0.3s ease;
}
.list-enter-from,
.list-leave-to {
  opacity: 0;
  transform: translateX(30px);
}
</style>
