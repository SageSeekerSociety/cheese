<!--
  What the security settings page shows (Security.vue): the ways in — password,
  passkeys, two-step verification, linked accounts — the signed-in devices, and
  the dialogs that change the password and set two-step verification up. It
  draws them from the props the container hands it, holds the fields it is
  filled with and which dialog is open, and reports what it was asked to do.
-->
<template>
  <div class="settings-page">
    <header>
      <h1 class="t-page-title">{{ t('account.security.title') }}</h1>
      <p class="settings-page__lede">{{ t('account.security.lede') }}</p>
    </header>

    <!-- Organised by the ways in, not by mechanism. The page has no single main
         action, so nothing on it is amber (design-system §1.6). -->
    <section class="settings-card">
      <h2 class="settings-card__title">{{ t('account.security.signIn') }}</h2>

      <div class="srow">
        <span class="srow__k">{{ t('account.security.password') }}</span>
        <span class="srow__v">{{ t('account.security.passwordNote') }}</span>
        <BaseButton kind="secondary" size="sm" @click="showChangePassword = true">
          {{ t('account.security.change') }}
        </BaseButton>
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
        <BaseButton
          kind="secondary"
          size="sm"
          :disabled="!webAuthnSupported"
          :loading="addingPasskey"
          @click="emit('add-passkey')"
        >
          {{ t('account.security.add') }}
        </BaseButton>
      </div>
      <div v-for="passkey in passkeys" :key="passkey.id" class="srow srow--sub">
        <span class="srow__k srow__k--quiet">
          <v-icon icon="mdi-key-variant" size="18" />
          {{ passkey.backedUp ? t('account.security.passkeySynced') : t('account.security.passkeyOneDevice') }}
        </span>
        <span class="srow__v">{{ t('account.security.addedOn', { date: formatDate(passkey.createdAt) }) }}</span>
        <BaseButton
          kind="ghost"
          size="sm"
          :loading="deletingPasskey === passkey.id"
          @click="emit('delete-passkey', passkey.id)"
        >
          {{ t('account.security.remove') }}
        </BaseButton>
      </div>

      <div class="srow">
        <span class="srow__k">{{ t('account.security.twoFactor') }}</span>
        <span class="srow__v">
          <span class="status-dot" :class="{ 'status-dot--on': totpEnabled }" aria-hidden="true" />
          {{ totpEnabled ? t('account.security.twoFactorOn') : t('account.security.twoFactorOff') }}
        </span>
        <BaseButton
          kind="secondary"
          size="sm"
          :loading="totpBusy"
          @click="totpEnabled ? emit('disable-totp') : emit('init-totp')"
        >
          {{ totpEnabled ? t('account.security.turnOff') : t('account.security.turnOn') }}
        </BaseButton>
      </div>

      <div v-if="totpEnabled" class="srow">
        <span class="srow__k">{{ t('account.security.backupCodes') }}</span>
        <span class="srow__v">{{ t('account.security.backupCodesNote') }}</span>
        <BaseButton kind="ghost" size="sm" :loading="generatingCodes" @click="emit('generate-backup-codes')">
          {{ t('account.security.regenerate') }}
        </BaseButton>
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
        <BaseButton kind="ghost" size="sm" :loading="unbinding === conn.id" @click="emit('unbind', conn)">
          {{ t('account.security.unlink') }}
        </BaseButton>
      </div>
      <div v-if="connectionsLoaded && !connections.length" class="srow srow--empty">
        {{ t('account.security.noConnections') }}
      </div>
    </section>

    <section class="settings-card">
      <div class="settings-card__head">
        <h2 class="settings-card__title">{{ t('account.security.sessions') }}</h2>
        <BaseButton
          v-if="sessions.some((s) => !s.current)"
          kind="ghost"
          size="sm"
          :loading="signingOutOthers"
          @click="emit('sign-out-others')"
        >
          {{ t('account.security.signOutOthers') }}
        </BaseButton>
      </div>

      <div v-for="session in sessions" :key="session.id" class="srow">
        <span class="srow__k">
          <v-icon :icon="sessionIcon(session.userAgent)" size="18" />
          {{ deviceLabel(session.userAgent) }}
        </span>
        <span class="srow__v srow__v--parts" :title="sessionDetails(session).join(' · ')">
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
        <BaseButton
          v-if="!session.current"
          kind="ghost"
          size="sm"
          :loading="signingOut === session.id"
          @click="emit('sign-out-device', session.id)"
        >
          {{ t('account.security.signOutDevice') }}
        </BaseButton>
      </div>
      <div v-if="sessionsLoaded && !sessions.length" class="srow srow--empty">
        {{ t('account.security.noSessions') }}
      </div>
    </section>

    <!-- Changing the password -->
    <v-dialog v-model="showChangePassword" :max-width="DIALOG_WIDTH.sm" @after-leave="resetPasswordForm">
      <v-card :title="t('account.security.changePasswordTitle')">
        <v-form ref="passwordForm" @submit.prevent="submitPassword">
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
            <BaseButton kind="ghost" @click="showChangePassword = false">{{ t('account.cancel') }}</BaseButton>
            <BaseButton kind="primary" type="submit" :loading="changingPassword">
              {{ t('account.security.changePasswordSubmit') }}
            </BaseButton>
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
                <BaseButton
                  icon="mdi-content-copy"
                  size="sm"
                  :aria-label="t('account.security.copy')"
                  @click="emit('copy', totpSecret)"
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
                @finish="emit('enable-totp')"
              />
              <p v-if="verifyError" class="setup__error">{{ verifyError }}</p>
            </div>

            <div v-else key="backup" class="setup">
              <p class="setup__lede">{{ t('account.security.codesLede') }}</p>
              <ul class="setup__codes">
                <li v-for="code in backupCodes" :key="code">{{ code }}</li>
              </ul>
              <BaseButton
                kind="secondary"
                size="sm"
                prepend-icon="mdi-content-copy"
                @click="emit('copy', backupCodes.join('\n'))"
              >
                {{ t('account.security.copyAll') }}
              </BaseButton>
            </div>
          </transition>
        </v-card-text>

        <v-card-actions>
          <BaseButton v-if="setupStep === 'verify'" kind="ghost" @click="setupStep = 'qr'">
            {{ t('account.security.back') }}
          </BaseButton>
          <v-spacer />
          <BaseButton v-if="setupStep !== 'backup'" kind="ghost" @click="emit('close-totp')">{{
            t('account.cancel')
          }}</BaseButton>
          <BaseButton v-if="setupStep === 'qr'" kind="primary" @click="setupStep = 'verify'">
            {{ t('account.security.next') }}
          </BaseButton>
          <BaseButton
            v-else-if="setupStep === 'verify'"
            kind="primary"
            :loading="totpBusy"
            :disabled="verificationCode.length !== 6"
            @click="emit('enable-totp')"
          >
            {{ t('account.security.verify') }}
          </BaseButton>
          <BaseButton v-else kind="primary" @click="emit('close-totp')">{{ t('account.security.done') }}</BaseButton>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import type { OAuthConnectionInfo } from '@/cx_types'
import type { PasskeyInfo, SessionInfo } from '@/network/api/users/types'

import { computed, ref } from 'vue'

import { REGEX_PASSWORD } from '@/utils/form'

import PasswordField from '@/components/account/PasswordField.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'
import i18n, { t } from '@/i18n'
import { oauthProviderIcon, oauthProviderName } from '@/views/account/oauthProvider'
import { deviceOf } from '@/views/user/settings/deviceName'

/** Which half of turning two-step verification on is shown. */
export type SetupStep = 'qr' | 'verify' | 'backup'

defineProps<{
  /** The browser can make passkeys at all; without it the row is inert. */
  webAuthnSupported: boolean
  passkeys: PasskeyInfo[]
  addingPasskey: boolean
  /** The id of the passkey whose removal is in flight, if any. */
  deletingPasskey: string | null
  totpEnabled: boolean
  totpBusy: boolean
  generatingCodes: boolean
  connections: OAuthConnectionInfo[]
  /** The connections came back at least once; an empty list is then "none". */
  connectionsLoaded: boolean
  /** The id of the connection whose removal is in flight, if any. */
  unbinding: number | null
  sessions: SessionInfo[]
  /** The sessions came back at least once; an empty list is then "none". */
  sessionsLoaded: boolean
  /** The id of the session being ended, if any. */
  signingOut: string | null
  signingOutOthers: boolean
  changingPassword: boolean
  /** The dialog reuses its last step to show fresh backup codes alone. */
  codesOnly: boolean
  totpSecret: string
  qrCodeData: string
  verifyError: string
  backupCodes: string[]
}>()

const emit = defineEmits<{
  'add-passkey': []
  'delete-passkey': [credentialId: string]
  'init-totp': []
  'disable-totp': []
  'enable-totp': []
  'generate-backup-codes': []
  'close-totp': []
  'change-password': [password: string]
  unbind: [conn: OAuthConnectionInfo]
  'sign-out-device': [sessionId: string]
  'sign-out-others': []
  copy: [text: string]
}>()

// Which dialog is open, and how far the two-step setup has got, are the
// container's to close as well as this side's to open.
const showChangePassword = defineModel<boolean>('showChangePassword', { required: true })
const showTotp = defineModel<boolean>('showTotp', { required: true })
const setupStep = defineModel<SetupStep>('setupStep', { required: true })
const verificationCode = defineModel<string>('verificationCode', { required: true })

// The change-password fields, their form, and the code being typed are this
// side's; only what is sent up leaves them.
const newPassword = ref('')
const confirmPassword = ref('')
const passwordForm = ref<{ validate: () => Promise<{ valid: boolean }>; reset: () => void } | null>(null)

function resetPasswordForm() {
  newPassword.value = ''
  confirmPassword.value = ''
  passwordForm.value?.reset()
}

const submitPassword = async () => {
  const { valid } = (await passwordForm.value?.validate()) ?? { valid: false }
  if (!valid) return
  emit('change-password', newPassword.value)
}

const STEPS: SetupStep[] = ['qr', 'verify', 'backup']
const stepIndex = computed(() => STEPS.indexOf(setupStep.value))

const formatDate = (value: string | Date) =>
  new Intl.DateTimeFormat(i18n.global.locale.value, { dateStyle: 'long' }).format(new Date(value))

const formatDateTime = (value: string | Date) =>
  new Intl.DateTimeFormat(i18n.global.locale.value, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))

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

// When, how it signed in, whether it may skip two-step verification, and from
// where — whichever of these is known.
const sessionDetails = (session: SessionInfo) =>
  [
    session.current
      ? t('account.security.thisDevice')
      : t('account.security.lastActive', { date: formatDateTime(session.lastActiveAt) }),
    methodLabel(session.loginMethod),
    session.trusted ? t('account.security.trusted') : '',
    session.ipAddress,
  ].filter(Boolean)

const sessionIcon = (userAgent: string) =>
  ['iOS', 'Android'].includes(deviceOf(userAgent)?.os ?? '') ? 'mdi-cellphone' : 'mdi-monitor'
</script>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
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

.srow__k--quiet {
  font-weight: 400;
  color: var(--text);
}

/* A row of parts joined by dots. Every part reserves the width of one
   separator after it, and every part but the first pulls its own separator
   back into that space. A part that wraps to the start of a line pulls its
   separator past the left edge instead, where the clip hides it.

   On the wide layout the row is one line: a device's details that wrap to a
   second line push the row taller than the sign-out button beside it and the
   button looks misaligned. So from the phone tier up the parts are laid out
   inline in a single clipped line — anything past the edge is hidden and the
   full text is on `title`. Below it the row has its own full-width line and
   wraps freely. The line used to be drawn at 600, which is not one of the four
   tiers; every settings row now folds at the same one. */
.srow__v--parts {
  --sep: 20px;

  column-gap: 0;
  row-gap: 2px;
  overflow: hidden;
}

/* 断点收进共享 token：767.98 = $bp-phone（styles/breakpoints.scss）；下面这条是 min-width。
   这一处原来是 600，归 767.98 会把 600–767 从单行截断改成换行 —— 真浏览器里量过这一带
   （视口 390/560/600/767/768/900/1440，种子里的 15 行设备记录）：两边的行高都是 19px、
   值区都没被裁、和右边那颗退出按钮都没有重叠，折叠点挪过来没改变任何一行的样子。 */
@media (min-width: 767.98px) {
  .srow__v--parts {
    display: block;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
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
</style>
