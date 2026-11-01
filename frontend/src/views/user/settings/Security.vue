<!--
  The security settings page: the ways into the account and the devices signed
  in to it. It reads passkeys, two-step verification, linked accounts and
  sessions, and runs changing the password and turning two-step verification on
  and off. What it shows is SecurityView.vue.
-->
<template>
  <SecurityView
    v-model:show-change-password="showChangePassword"
    v-model:show-totp="showTotp"
    v-model:setup-step="setupStep"
    v-model:verification-code="verificationCode"
    v-model:show-close-account="showCloseAccount"
    :username="currentUserName ?? ''"
    :closing-account="closingAccount"
    :close-error="closeError"
    :web-authn-supported="webAuthnSupported"
    :passkeys="passkeys"
    :adding-passkey="addingPasskey"
    :deleting-passkey="deletingPasskey"
    :totp-enabled="totpEnabled"
    :totp-busy="totpBusy"
    :generating-codes="generatingCodes"
    :connections="connections"
    :connections-loaded="connectionsLoaded"
    :unbinding="unbinding"
    :sessions="sessions"
    :sessions-loaded="sessionsLoaded"
    :signing-out="signingOut"
    :signing-out-others="signingOutOthers"
    :changing-password="changingPassword"
    :codes-only="codesOnly"
    :totp-secret="totpSecret"
    :qr-code-data="qrCodeData"
    :verify-error="verifyError"
    :backup-codes="backupCodes"
    @add-passkey="handleAddPasskey"
    @delete-passkey="handleDeletePasskey"
    @init-totp="handleInitTOTP"
    @disable-totp="handleDisableTOTP"
    @enable-totp="handleEnableTOTP"
    @generate-backup-codes="handleGenerateBackupCodes"
    @close-totp="closeTotp"
    @change-password="handleChangePassword"
    @unbind="handleUnbind"
    @sign-in-github="signInWithGithub"
    @sign-out-device="signOutDevice"
    @sign-out-others="handleSignOutOthers"
    @copy="copy"
    @close-account="handleCloseAccount"
  />
</template>

<script setup lang="ts">
import type { OAuthConnectionInfo } from '@/cx_types'
import type { PasskeyInfo, SessionInfo } from '@/types/apiUsers'
import type { SetupStep } from './SecurityView.vue'

import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { browserSupportsWebAuthn, startRegistration } from '@simplewebauthn/browser'

import { SudoCancelledError, withSudo } from '@/utils/sudo'

import { signInWithGithub } from '@/composables/useGithubSignIn'

import SecurityView from './SecurityView.vue'

import { ApiError, deleteOAuthConnection, listOAuthConnections } from '@/api'
import { axiosErrorMessage } from '@/api/axiosHttp'
import { UserApi } from '@/api/users'
import { t } from '@/i18n'
import { GITHUB_APP_PROVIDER_ID } from '@/lib/githubAccount'
import { toast } from '@/lib/toast'
import { useDialog } from '@/plugins/dialog'
import AccountService, { currentUserId, currentUserName } from '@/services/account'
import { oauthProviderName } from '@/views/account/oauthProvider'
import { passkeyWrongHostMessage } from '@/views/account/passkeyHost'

const dialogs = useDialog()
const router = useRouter()
const webAuthnSupported = browserSupportsWebAuthn()

const fail = (error: unknown, fallback: string) => toast.error(axiosErrorMessage(error, fallback))

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(t('account.security.copied'))
  } catch {
    toast.error(t('account.security.copyFailed'))
  }
}

// ---- Password ----

const showChangePassword = ref(false)
const changingPassword = ref(false)

const handleChangePassword = async (password: string) => {
  changingPassword.value = true
  try {
    await submitNewPassword(password)
    showChangePassword.value = false
  } catch (error) {
    if (error instanceof SudoCancelledError) return
    fail(error, t('account.security.changePasswordFailed'))
  } finally {
    changingPassword.value = false
  }
}

const submitNewPassword = (password: string) =>
  withSudo('password:change', async (sudoTicket) => {
    if (!currentUserId.value) return
    await UserApi.changePassword(currentUserId.value, { password, sudoTicket })
    toast.success(t('account.security.passwordChanged'))
    // Every other device was signed out with it.
    await fetchSessions()
  })

// ---- Passkeys ----

const passkeys = ref<PasskeyInfo[]>([])
const addingPasskey = ref(false)
const deletingPasskey = ref<string | null>(null)

const fetchPasskeys = async () => {
  if (!currentUserId.value) return
  try {
    const data = await UserApi.getUserPasskeys(currentUserId.value)
    passkeys.value = data.passkeys
  } catch (error) {
    fail(error, t('account.security.loadFailed'))
  }
}

const handleAddPasskey = async () => {
  if (!currentUserId.value) return
  addingPasskey.value = true
  let rpId: string | undefined
  try {
    await withSudo('passkey:add', async (sudoTicket) => {
      const data = await UserApi.getPasskeyRegistrationOptions(currentUserId.value!, sudoTicket)
      rpId = data.options.rp?.id
      const attestation = await startRegistration({ optionsJSON: data.options })
      await UserApi.verifyPasskeyRegistration(currentUserId.value!, attestation)
      await fetchPasskeys()
      toast.success(t('account.security.passkeyAddedToast'))
    })
  } catch (error: any) {
    if (error instanceof SudoCancelledError) return
    // The browser's own WebAuthn error text is English and names internals.
    const wrongHost = passkeyWrongHostMessage(error, rpId)
    if (wrongHost) toast.error(wrongHost)
    else if (error?.name === 'InvalidStateError') toast.error(t('account.security.passkeyExists'))
    else if (error?.name === 'NotAllowedError') toast.error(t('account.security.passkeyCanceled'))
    else fail(error, t('account.security.passkeyAddFailed'))
  } finally {
    addingPasskey.value = false
  }
}

const handleDeletePasskey = async (credentialId: string) => {
  const confirmed = await dialogs
    .confirm(t('account.security.removePasskeyBody'), { title: t('account.security.removePasskeyTitle') })
    .wait()
  if (confirmed) await deletePasskey(credentialId)
}

const deletePasskey = async (credentialId: string) => {
  if (!currentUserId.value) return
  deletingPasskey.value = credentialId
  try {
    await withSudo('passkey:delete', async (sudoTicket) => {
      await UserApi.deletePasskey(currentUserId.value!, credentialId, sudoTicket)
      await fetchPasskeys()
      toast.success(t('account.security.passkeyRemoved'))
    })
  } catch (error) {
    if (error instanceof SudoCancelledError) return
    fail(error, t('account.security.passkeyRemoveFailed'))
  } finally {
    deletingPasskey.value = null
  }
}

// ---- Two-step verification ----

const totpEnabled = ref(false)
const totpBusy = ref(false)
const generatingCodes = ref(false)
const showTotp = ref(false)
// Showing freshly generated backup codes reuses the last step on its own.
const codesOnly = ref(false)
const setupStep = ref<SetupStep>('qr')
const totpSecret = ref('')
const qrCodeData = ref('')
const verificationCode = ref('')
const verifyError = ref('')
const backupCodes = ref<string[]>([])

const fetch2FAStatus = async () => {
  if (!currentUserId.value) return
  try {
    const data = await UserApi.get2FAStatus(currentUserId.value)
    totpEnabled.value = data.enabled
  } catch (error) {
    fail(error, t('account.security.loadFailed'))
  }
}

const handleInitTOTP = async () => {
  if (!currentUserId.value) return
  totpBusy.value = true
  try {
    await withSudo('2fa:enable', async (sudoTicket) => {
      const data = await UserApi.initializeTOTP(currentUserId.value!, sudoTicket)
      totpSecret.value = data.secret
      qrCodeData.value = data.qrcode
      codesOnly.value = false
      setupStep.value = 'qr'
      showTotp.value = true
    })
  } catch (error) {
    if (error instanceof SudoCancelledError) return
    fail(error, t('account.security.setupFailed'))
  } finally {
    totpBusy.value = false
  }
}

const handleEnableTOTP = async () => {
  if (!currentUserId.value || verificationCode.value.length !== 6 || totpBusy.value) return
  totpBusy.value = true
  verifyError.value = ''
  try {
    const data = await UserApi.enableTOTP(currentUserId.value, {
      code: verificationCode.value,
      secret: totpSecret.value,
    })
    backupCodes.value = data.backup_codes
    setupStep.value = 'backup'
    await fetch2FAStatus()
  } catch (error) {
    verificationCode.value = ''
    verifyError.value = axiosErrorMessage(error, t('account.security.wrongCode'))
  } finally {
    totpBusy.value = false
  }
}

function closeTotp() {
  showTotp.value = false
  verificationCode.value = ''
  verifyError.value = ''
  backupCodes.value = []
  totpSecret.value = ''
  qrCodeData.value = ''
}

const handleDisableTOTP = async () => {
  const confirmed = await dialogs
    .confirm(t('account.security.turnOffBody'), {
      title: t('account.security.turnOffTitle'),
      confirmLabel: t('account.security.turnOff'),
      danger: true,
    })
    .wait()
  if (confirmed) await disableTOTP()
}

const disableTOTP = async () => {
  if (!currentUserId.value) return
  totpBusy.value = true
  try {
    await withSudo('2fa:disable', async (sudoTicket) => {
      await UserApi.disableTOTP(currentUserId.value!, sudoTicket)
      await fetch2FAStatus()
      toast.success(t('account.security.turnedOff'))
    })
  } catch (error) {
    if (error instanceof SudoCancelledError) return
    fail(error, t('account.security.turnOffFailed'))
  } finally {
    totpBusy.value = false
  }
}

const handleGenerateBackupCodes = async () => {
  const confirmed = await dialogs
    .confirm(t('account.security.regenerateBody'), {
      title: t('account.security.regenerateTitle'),
      confirmLabel: t('account.security.regenerate'),
      danger: true,
    })
    .wait()
  if (confirmed) await generateBackupCodes()
}

const generateBackupCodes = async () => {
  if (!currentUserId.value) return
  generatingCodes.value = true
  try {
    await withSudo('2fa:backup-codes', async (sudoTicket) => {
      const data = await UserApi.generateBackupCodes(currentUserId.value!, sudoTicket)
      backupCodes.value = data.backup_codes
      codesOnly.value = true
      setupStep.value = 'backup'
      showTotp.value = true
    })
  } catch (error) {
    if (error instanceof SudoCancelledError) return
    fail(error, t('account.security.regenerateFailed'))
  } finally {
    generatingCodes.value = false
  }
}

// ---- Linked accounts ----

const connections = ref<OAuthConnectionInfo[]>([])
const connectionsLoaded = ref(false)
const unbinding = ref<number | null>(null)

const fetchConnections = async () => {
  if (!currentUserId.value) return
  try {
    const { connections: all } = await listOAuthConnections(String(currentUserId.value))
    connections.value = all
  } catch (error) {
    fail(error, t('account.security.loadFailed'))
  } finally {
    connectionsLoaded.value = true
  }
}

const handleUnbind = async (conn: OAuthConnectionInfo) => {
  const provider = oauthProviderName(conn.providerId)
  // GitHub does a second job besides signing in: unlinking it moves the PRs of
  // every task this person owns back to the AI teammate, and they should know.
  const body =
    conn.providerId === GITHUB_APP_PROVIDER_ID
      ? t('account.security.githubUnlinkBody')
      : t('account.security.unlinkBody', { provider })
  const confirmed = await dialogs.confirm(body, { title: t('account.security.unlinkTitle', { provider }) }).wait()
  if (confirmed) await unbind(conn.id)
}

const unbind = async (connectionId: number) => {
  if (!currentUserId.value) return
  unbinding.value = connectionId
  try {
    await withSudo('oauth:unbind', async (sudoTicket) => {
      await deleteOAuthConnection(String(currentUserId.value), connectionId, sudoTicket)
      await fetchConnections()
      toast.success(t('account.security.unlinked'))
    })
  } catch (error) {
    if (error instanceof SudoCancelledError) return
    // The server refuses to remove the last way in (409).
    const lastWayIn = error instanceof ApiError && error.status === 409
    toast.error(lastWayIn ? t('account.security.lastWayIn') : t('account.security.unlinkFailed'))
  } finally {
    unbinding.value = null
  }
}

// ---- Signed-in devices ----

const sessions = ref<SessionInfo[]>([])
const sessionsLoaded = ref(false)
const signingOut = ref<string | null>(null)
const signingOutOthers = ref(false)

const fetchSessions = async () => {
  try {
    const data = await UserApi.listSessions()
    sessions.value = data.sessions
  } catch (error) {
    fail(error, t('account.security.loadFailed'))
  } finally {
    sessionsLoaded.value = true
  }
}

const signOutDevice = async (sessionId: string) => {
  signingOut.value = sessionId
  try {
    await UserApi.revokeSession(sessionId)
    await fetchSessions()
    toast.success(t('account.security.signedOutDevice'))
  } catch (error) {
    fail(error, t('account.security.signOutFailed'))
  } finally {
    signingOut.value = null
  }
}

const handleSignOutOthers = async () => {
  const confirmed = await dialogs
    .confirm(t('account.security.signOutOthersBody'), { title: t('account.security.signOutOthersTitle') })
    .wait()
  if (!confirmed) return
  signingOutOthers.value = true
  try {
    const data = await UserApi.revokeOtherSessions()
    await fetchSessions()
    toast.success(t('account.security.signedOutOthers', { count: data.revokedCount }))
  } catch (error) {
    fail(error, t('account.security.signOutFailed'))
  } finally {
    signingOutOthers.value = false
  }
}

// ---- Closing the account ----

const showCloseAccount = ref(false)
const closingAccount = ref(false)
const closeError = ref('')
watch(showCloseAccount, () => (closeError.value = ''))

const handleCloseAccount = async (username: string) => {
  closingAccount.value = true
  closeError.value = ''
  try {
    await withSudo('account:delete', (sudoTicket) => UserApi.closeAccount(username, sudoTicket))
  } catch (error) {
    closingAccount.value = false
    if (error instanceof SudoCancelledError) return
    // In the dialog, not a toast: a refusal such as a team still to hand on
    // says what to do first, and has to stay readable while they go do it.
    closeError.value = axiosErrorMessage(error, t('account.security.closeFailed'))
    return
  }
  // The account is gone: nothing this tab holds signs in as it any more.
  showCloseAccount.value = false
  toast.success(t('account.security.closed'))
  await AccountService.logout()
  await router.push('/')
}

onMounted(async () => {
  if (webAuthnSupported) fetchPasskeys()
  fetch2FAStatus()
  fetchConnections()
  fetchSessions()
})
</script>
