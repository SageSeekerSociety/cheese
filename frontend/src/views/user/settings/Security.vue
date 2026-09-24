<template>
  <div class="security">
    <header class="security__head">
      <h1 class="t-page-title">{{ t('account.security.title') }}</h1>
      <p class="security__lede">{{ t('account.security.lede') }}</p>
    </header>

    <!-- Organised by the ways in, not by mechanism. The page has no single main
         action, so nothing on it is amber (design-system §1.6). -->
    <section class="settings-card">
      <h2 class="settings-card__title">{{ t('account.security.signIn') }}</h2>

      <div class="srow">
        <span class="srow__k">{{ t('account.security.password') }}</span>
        <span class="srow__v">{{ t('account.security.passwordNote') }}</span>
        <v-btn variant="outlined" color="on-surface" size="small" @click="showChangePassword = true">
          {{ t('account.security.change') }}
        </v-btn>
      </div>

      <div class="srow">
        <span class="srow__k">{{ t('account.security.passkeys') }}</span>
        <span class="srow__v">
          <template v-if="!webAuthnSupported">{{ t('account.security.passkeysUnsupported') }}</template>
          <template v-else-if="passkeys.length">{{
            t('account.security.passkeyCount', { count: passkeys.length })
          }}</template>
          <template v-else>{{ t('account.security.noPasskeys') }}</template>
        </span>
        <v-btn
          variant="outlined"
          color="on-surface"
          size="small"
          :disabled="!webAuthnSupported"
          :loading="addingPasskey"
          @click="handleAddPasskey"
        >
          {{ t('account.security.add') }}
        </v-btn>
      </div>
      <div v-for="passkey in passkeys" :key="passkey.id" class="srow srow--sub">
        <span class="srow__k srow__k--quiet">
          <v-icon icon="mdi-key-variant" size="18" />
          {{ passkey.backedUp ? t('account.security.passkeySynced') : t('account.security.passkeyOneDevice') }}
        </span>
        <span class="srow__v">{{ t('account.security.addedOn', { date: formatDate(passkey.createdAt) }) }}</span>
        <v-btn
          variant="text"
          color="on-surface"
          size="small"
          :loading="deletingPasskey === passkey.id"
          @click="handleDeletePasskey(passkey.id)"
        >
          {{ t('account.security.remove') }}
        </v-btn>
      </div>

      <div class="srow">
        <span class="srow__k">{{ t('account.security.twoFactor') }}</span>
        <span class="srow__v">
          <span class="status-dot" :class="{ 'status-dot--on': totpEnabled }" aria-hidden="true" />
          {{ totpEnabled ? t('account.security.twoFactorOn') : t('account.security.twoFactorOff') }}
        </span>
        <v-btn
          variant="outlined"
          color="on-surface"
          size="small"
          :loading="totpBusy"
          @click="totpEnabled ? handleDisableTOTP() : handleInitTOTP()"
        >
          {{ totpEnabled ? t('account.security.turnOff') : t('account.security.turnOn') }}
        </v-btn>
      </div>

      <div v-if="totpEnabled" class="srow">
        <span class="srow__k">{{ t('account.security.backupCodes') }}</span>
        <span class="srow__v">{{ t('account.security.backupCodesNote') }}</span>
        <v-btn
          variant="text"
          color="on-surface"
          size="small"
          :loading="generatingCodes"
          @click="handleGenerateBackupCodes"
        >
          {{ t('account.security.regenerate') }}
        </v-btn>
      </div>
    </section>

    <section class="settings-card">
      <h2 class="settings-card__title">{{ t('account.security.connections') }}</h2>
      <p class="settings-card__desc">{{ t('account.security.connectionsNote') }}</p>

      <div v-for="conn in connections" :key="conn.id" class="srow">
        <span class="srow__k">
          <v-icon :icon="oauthProviderIcon(conn.providerId)" size="18" />
          {{ oauthProviderName(conn.providerId) }}
        </span>
        <span class="srow__v">
          <span v-if="conn.login" class="srow__strong">@{{ conn.login }}</span>
          <template v-if="conn.connectedAt">{{
            t('account.security.linkedOn', { date: formatDate(conn.connectedAt) })
          }}</template>
        </span>
        <v-btn
          variant="text"
          color="on-surface"
          size="small"
          :loading="unbinding === conn.id"
          @click="handleUnbind(conn)"
        >
          {{ t('account.security.unlink') }}
        </v-btn>
      </div>
      <div v-if="connectionsLoaded && !connections.length" class="srow srow--empty">
        {{ t('account.security.noConnections') }}
      </div>
    </section>

    <section class="settings-card">
      <div class="settings-card__head">
        <h2 class="settings-card__title">{{ t('account.security.sessions') }}</h2>
        <v-btn
          v-if="sessions.some((s) => !s.current)"
          variant="text"
          color="on-surface"
          size="small"
          :loading="signingOutOthers"
          @click="handleSignOutOthers"
        >
          {{ t('account.security.signOutOthers') }}
        </v-btn>
      </div>

      <div v-for="session in sessions" :key="session.id" class="srow">
        <span class="srow__k">
          <v-icon :icon="sessionIcon(session.userAgent)" size="18" />
          {{ deviceLabel(session.userAgent) }}
        </span>
        <span class="srow__v srow__v--parts">
          <!-- The separator belongs to the part after it, so a wrapped row
               never leaves a dot alone at the end of a line; one that lands
               at the start of a line is clipped (see .srow__v--parts). -->
          <span
            v-for="(part, i) in sessionDetails(session)"
            :key="i"
            class="srow__part"
            :class="{ srow__strong: i === 0 && session.current }"
            ><span v-if="i" class="srow__sep" aria-hidden="true">·</span>{{ part }}</span
          >
        </span>
        <v-btn
          v-if="!session.current"
          variant="text"
          color="on-surface"
          size="small"
          :loading="signingOut === session.id"
          @click="signOutDevice(session.id)"
        >
          {{ t('account.security.signOutDevice') }}
        </v-btn>
      </div>
      <div v-if="sessionsLoaded && !sessions.length" class="srow srow--empty">
        {{ t('account.security.noSessions') }}
      </div>
    </section>

    <!-- Changing the password -->
    <v-dialog v-model="showChangePassword" max-width="440" @after-leave="resetPasswordForm">
      <v-card :title="t('account.security.changePasswordTitle')">
        <v-form ref="passwordForm" @submit.prevent="handleChangePassword">
          <v-card-text class="pt-2">
            <PasswordField
              id="security-new-password"
              v-model="newPassword"
              autocomplete="new-password"
              :label="t('account.field.newPassword')"
              :hint="t('account.rule.passwordHint')"
              persistent-hint
              :rules="[(v: string) => REGEX_PASSWORD.test(v ?? '') || t('account.rule.passwordInvalid')]"
              class="mb-2"
            />
            <PasswordField
              id="security-confirm-password"
              v-model="confirmPassword"
              autocomplete="new-password"
              :label="t('account.field.confirmPassword')"
              :rules="[(v: string) => v === newPassword || t('account.rule.passwordsDoNotMatch')]"
            />
          </v-card-text>
          <v-card-actions>
            <v-spacer />
            <v-btn variant="text" @click="showChangePassword = false">{{ t('account.cancel') }}</v-btn>
            <v-btn color="primary" variant="flat" type="submit" :loading="changingPassword">
              {{ t('account.security.changePasswordSubmit') }}
            </v-btn>
          </v-card-actions>
        </v-form>
      </v-card>
    </v-dialog>

    <!-- Turning on two-step verification, and showing new backup codes -->
    <v-dialog v-model="showTotp" max-width="480" persistent>
      <v-card>
        <v-card-item>
          <v-card-title>
            {{ codesOnly ? t('account.security.newCodesTitle') : t('account.security.setupTitle') }}
          </v-card-title>
          <v-card-subtitle v-if="!codesOnly">
            {{ t('account.security.step', { step: stepIndex + 1, total: 3 }) }}
          </v-card-subtitle>
        </v-card-item>

        <v-card-text>
          <transition name="setup-step" mode="out-in">
            <div v-if="setupStep === 'qr'" key="qr" class="setup">
              <p class="setup__lede">{{ t('account.security.scanLede') }}</p>
              <img :src="qrCodeData" :alt="t('account.security.qrAlt')" class="setup__qr" width="200" height="200" />
              <p class="setup__manual">{{ t('account.security.manualKey') }}</p>
              <div class="setup__secret">
                <code>{{ totpSecret }}</code>
                <v-btn
                  icon="mdi-content-copy"
                  size="small"
                  variant="text"
                  :aria-label="t('account.security.copy')"
                  @click="copy(totpSecret)"
                />
              </div>
            </div>

            <div v-else-if="setupStep === 'verify'" key="verify" class="setup">
              <p class="setup__lede">{{ t('account.security.verifyLede') }}</p>
              <v-otp-input
                v-model="verificationCode"
                autofocus
                length="6"
                type="number"
                variant="outlined"
                :error="!!verifyError"
                @finish="handleEnableTOTP"
              />
              <p v-if="verifyError" class="setup__error">{{ verifyError }}</p>
            </div>

            <div v-else key="backup" class="setup">
              <p class="setup__lede">{{ t('account.security.codesLede') }}</p>
              <ul class="setup__codes">
                <li v-for="code in backupCodes" :key="code">{{ code }}</li>
              </ul>
              <v-btn
                variant="outlined"
                color="on-surface"
                size="small"
                prepend-icon="mdi-content-copy"
                @click="copy(backupCodes.join('\n'))"
              >
                {{ t('account.security.copyAll') }}
              </v-btn>
            </div>
          </transition>
        </v-card-text>

        <v-card-actions>
          <v-btn v-if="setupStep === 'verify'" variant="text" @click="setupStep = 'qr'">
            {{ t('account.security.back') }}
          </v-btn>
          <v-spacer />
          <v-btn v-if="setupStep !== 'backup'" variant="text" @click="closeTotp">{{ t('account.cancel') }}</v-btn>
          <v-btn v-if="setupStep === 'qr'" color="primary" variant="flat" @click="setupStep = 'verify'">
            {{ t('account.security.next') }}
          </v-btn>
          <v-btn
            v-else-if="setupStep === 'verify'"
            color="primary"
            variant="flat"
            :loading="totpBusy"
            :disabled="verificationCode.length !== 6"
            @click="handleEnableTOTP"
          >
            {{ t('account.security.verify') }}
          </v-btn>
          <v-btn v-else color="primary" variant="flat" @click="closeTotp">{{ t('account.security.done') }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import type { OAuthConnectionInfo } from '@/cx_types'
import type { PasskeyInfo, SessionInfo } from '@/network/api/users/types'

import { computed, onMounted, ref } from 'vue'
import { toast } from 'vuetify-sonner'
import { browserSupportsWebAuthn, startRegistration } from '@simplewebauthn/browser'

import { REGEX_PASSWORD } from '@/utils/form'
import { SudoCancelledError, withSudo } from '@/utils/sudo'

import { deleteOAuthConnection, listOAuthConnections } from '@/api'
import PasswordField from '@/components/account/PasswordField.vue'
import i18n, { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { useDialog } from '@/plugins/dialog'
import { currentUserId } from '@/services/account'
import { oauthProviderIcon, oauthProviderName } from '@/views/account/oauthProvider'
import { passkeyWrongHostMessage } from '@/views/account/passkeyHost'
import { deviceOf } from '@/views/user/settings/deviceName'

const dialogs = useDialog()
const webAuthnSupported = browserSupportsWebAuthn()

const fail = (error: unknown, fallback: string) => toast.error(requestErrorMessage(error, fallback))

const formatDate = (value: string | Date) =>
  new Intl.DateTimeFormat(i18n.global.locale.value, { dateStyle: 'long' }).format(new Date(value))

const formatDateTime = (value: string | Date) =>
  new Intl.DateTimeFormat(i18n.global.locale.value, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))

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
const newPassword = ref('')
const confirmPassword = ref('')
const changingPassword = ref(false)
const passwordForm = ref<{ validate: () => Promise<{ valid: boolean }>; reset: () => void } | null>(null)

function resetPasswordForm() {
  newPassword.value = ''
  confirmPassword.value = ''
  passwordForm.value?.reset()
}

const handleChangePassword = async () => {
  const { valid } = (await passwordForm.value?.validate()) ?? { valid: false }
  if (!valid) return
  changingPassword.value = true
  try {
    await submitNewPassword(newPassword.value)
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
    const { data } = await UserApi.getUserPasskeys(currentUserId.value)
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
      const { data } = await UserApi.getPasskeyRegistrationOptions(currentUserId.value!, sudoTicket)
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

type SetupStep = 'qr' | 'verify' | 'backup'
const STEPS: SetupStep[] = ['qr', 'verify', 'backup']

const totpEnabled = ref(false)
const totpBusy = ref(false)
const generatingCodes = ref(false)
const showTotp = ref(false)
// Showing freshly generated backup codes reuses the last step on its own.
const codesOnly = ref(false)
const setupStep = ref<SetupStep>('qr')
const stepIndex = computed(() => STEPS.indexOf(setupStep.value))
const totpSecret = ref('')
const qrCodeData = ref('')
const verificationCode = ref('')
const verifyError = ref('')
const backupCodes = ref<string[]>([])

const fetch2FAStatus = async () => {
  if (!currentUserId.value) return
  try {
    const { data } = await UserApi.get2FAStatus(currentUserId.value)
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
      const { data } = await UserApi.initializeTOTP(currentUserId.value!, sudoTicket)
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
    const { data } = await UserApi.enableTOTP(currentUserId.value, {
      code: verificationCode.value,
      secret: totpSecret.value,
    })
    backupCodes.value = data.backup_codes
    setupStep.value = 'backup'
    await fetch2FAStatus()
  } catch (error) {
    verificationCode.value = ''
    verifyError.value = requestErrorMessage(error, t('account.security.wrongCode'))
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
    .confirm(t('account.security.turnOffBody'), { title: t('account.security.turnOffTitle') })
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
    .confirm(t('account.security.regenerateBody'), { title: t('account.security.regenerateTitle') })
    .wait()
  if (confirmed) await generateBackupCodes()
}

const generateBackupCodes = async () => {
  if (!currentUserId.value) return
  generatingCodes.value = true
  try {
    await withSudo('2fa:backup-codes', async (sudoTicket) => {
      const { data } = await UserApi.generateBackupCodes(currentUserId.value!, sudoTicket)
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

// Only the accounts that sign in. A repository link (the GitHub app) is managed
// with its project and never signs anyone in.
const LINK_ONLY = new Set(['github_app'])
const connections = ref<OAuthConnectionInfo[]>([])
const connectionsLoaded = ref(false)
const unbinding = ref<number | null>(null)

const fetchConnections = async () => {
  if (!currentUserId.value) return
  try {
    const { connections: all } = await listOAuthConnections(String(currentUserId.value))
    connections.value = all.filter((c) => !LINK_ONLY.has(c.providerId))
  } catch (error) {
    fail(error, t('account.security.loadFailed'))
  } finally {
    connectionsLoaded.value = true
  }
}

const handleUnbind = async (conn: OAuthConnectionInfo) => {
  const provider = oauthProviderName(conn.providerId)
  const confirmed = await dialogs
    .confirm(t('account.security.unlinkBody', { provider }), { title: t('account.security.unlinkTitle', { provider }) })
    .wait()
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
    const lastWayIn = error instanceof Error && /HTTP 409/.test(error.message)
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
    const { data } = await UserApi.listSessions()
    sessions.value = data.sessions
  } catch (error) {
    fail(error, t('account.security.loadFailed'))
  } finally {
    sessionsLoaded.value = true
  }
}

const deviceLabel = (userAgent: string) => {
  const device = deviceOf(userAgent)
  return device ? t('account.security.deviceName', device) : t('account.security.unknownDevice')
}

const METHOD_LABELS: Record<string, () => string> = {
  password: () => t('account.security.methodPassword'),
  passkey: () => t('account.security.methodPasskey'),
  totp: () => t('account.security.methodTotp'),
  backup_code: () => t('account.security.methodBackupCode'),
  email_code: () => t('account.security.methodEmailCode'),
  signup: () => t('account.security.methodSignup'),
}

const methodLabel = (method: string) =>
  method.startsWith('oauth:')
    ? t('account.security.methodOAuth', { provider: oauthProviderName(method.slice('oauth:'.length)) })
    : METHOD_LABELS[method]?.() ?? ''

// When, how it signed in, and from where — whichever of these is known.
const sessionDetails = (session: SessionInfo) =>
  [
    session.current
      ? t('account.security.thisDevice')
      : t('account.security.lastActive', { date: formatDateTime(session.lastActiveAt) }),
    methodLabel(session.loginMethod),
    session.ipAddress,
  ].filter(Boolean)

const sessionIcon = (userAgent: string) =>
  ['iOS', 'Android'].includes(deviceOf(userAgent)?.os ?? '') ? 'mdi-cellphone' : 'mdi-monitor'

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
    const { data } = await UserApi.revokeOtherSessions()
    await fetchSessions()
    toast.success(t('account.security.signedOutOthers', { count: data.revokedCount }))
  } catch (error) {
    fail(error, t('account.security.signOutFailed'))
  } finally {
    signingOutOthers.value = false
  }
}

onMounted(async () => {
  if (webAuthnSupported) fetchPasskeys()
  fetch2FAStatus()
  fetchConnections()
  fetchSessions()
})
</script>

<style scoped>
.security {
  display: grid;
  gap: 20px;
  max-width: var(--page-w, 920px);
  padding: 24px 32px 48px;
}

.security__lede {
  margin-top: 4px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
}

.settings-card {
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.settings-card__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  padding-right: 16px;
}

/* Centred on the title's line, not on the head as a whole. */
.settings-card__head > .v-btn {
  flex-shrink: 0;
  margin-top: 16px;
}

.settings-card__title {
  padding: 20px 24px 8px;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

.settings-card__desc {
  padding: 0 24px 12px;
  margin-top: -4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.srow {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr) auto;
  gap: 16px;
  align-items: center;
  min-height: 56px;
  padding: 10px 24px;
  border-top: 1px solid var(--line);
}

/* A passkey sits under the passkey row, as its detail; the columns stay put. */
.srow--sub {
  min-height: 48px;
}

.srow--sub .srow__k {
  padding-left: 16px;
}

.srow--empty {
  display: block;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.srow__k {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 14px;
  font-weight: 500;
  line-height: var(--lh-14);
  color: var(--ink);
}

.srow__k--quiet {
  font-weight: 400;
  color: var(--text);
}

.srow__k .v-icon {
  color: var(--muted);
}

.srow__v {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.srow__strong {
  color: var(--text);
}

/* A row of parts joined by dots. Every part reserves the width of one
   separator after it, and every part but the first pulls its own separator
   back into that space. A part that wraps to the start of a line pulls its
   separator past the left edge instead, where the clip hides it. */
.srow__v--parts {
  --sep: 20px;

  column-gap: 0;
  row-gap: 2px;
  overflow: hidden;
}

.srow__part {
  margin-right: var(--sep);
  white-space: nowrap;
}

.srow__part + .srow__part {
  margin-left: calc(-1 * var(--sep));
}

.srow__sep {
  display: inline-block;
  width: var(--sep);
  color: var(--faint);
  text-align: center;
}

.status-dot {
  width: 8px;
  height: 8px;
  background: var(--line-2);
  border-radius: var(--radius-pill);
}

.status-dot--on {
  background: var(--ok);
}

/* ---- Two-step setup ---- */

.setup {
  display: grid;
  gap: 12px;
  justify-items: center;
  text-align: center;
}

.setup__lede {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.setup__qr {
  display: block;
  padding: 8px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.setup__manual {
  margin-top: 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.setup__secret {
  display: flex;
  gap: 4px;
  align-items: center;
  font-family: var(--font-mono);
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  word-break: break-all;
}

.setup__error {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--danger-ink);
}

.setup__codes {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 24px;
  width: 100%;
  padding: 16px;
  font-family: var(--font-mono);
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  list-style: none;
  background: var(--fill);
  border-radius: var(--radius-md);
}

.setup-step-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}

.setup-step-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}

.setup-step-enter-from {
  opacity: 0;
  transform: translateX(8px);
}

.setup-step-leave-to {
  opacity: 0;
}

@media (max-width: 599.98px) {
  .security {
    padding: 16px 16px 32px;
  }

  .srow {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 4px 16px;
    padding: 12px 16px;
  }

  .srow__v {
    grid-row: 2;
    grid-column: 1 / -1;
  }

  .settings-card__title {
    padding: 16px 16px 8px;
  }

  .settings-card__desc {
    padding: 0 16px 12px;
  }
}
</style>
