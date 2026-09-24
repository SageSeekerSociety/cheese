<template>
  <v-dialog
    :model-value="request !== null"
    max-width="420"
    :aria-labelledby="titleId"
    @update:model-value="(open: boolean) => !open && cancel()"
    @after-enter="focusFirst"
    @after-leave="restoreFocus"
  >
    <v-card rounded="lg" class="sudo">
      <h2 :id="titleId" class="sudo__title">{{ t('account.sudo.title') }}</h2>
      <p class="sudo__lede">{{ lede }}</p>

      <v-alert v-if="errorMessage" type="error" density="comfortable" class="sudo__error">
        {{ errorMessage }}
      </v-alert>

      <div v-if="methods === null" class="sudo__loading">
        <v-progress-linear indeterminate color="primary" height="2" />
      </div>

      <p v-else-if="!available.length" class="sudo__empty">{{ t('account.sudo.noMethod') }}</p>

      <template v-else>
        <transition name="sudo-method" mode="out-in" @after-enter="focusFirst">
          <div :key="method" ref="panel">
            <v-btn
              v-if="method === 'passkey'"
              block
              color="primary"
              size="large"
              class="sudo__submit"
              :loading="loading"
              @click="verifyPasskey"
            >
              <v-icon start icon="mdi-key-chain" size="20" />
              {{ t('account.sudo.passkey') }}
            </v-btn>

            <v-form v-else-if="method === 'password'" @submit.prevent="verifyPassword">
              <!-- Tells a password manager whose password this is, so it can fill it. -->
              <input
                class="sudo__username"
                type="text"
                name="username"
                autocomplete="username"
                :value="currentUserName"
                readonly
                tabindex="-1"
                aria-hidden="true"
              />
              <AccountField :label="t('account.field.password')" input-id="sudo-password">
                <PasswordField
                  id="sudo-password"
                  v-model="password"
                  autocomplete="current-password"
                  name="password"
                  hide-details
                />
              </AccountField>
              <v-btn block color="primary" size="large" type="submit" class="sudo__submit" :loading="loading">
                {{ t('account.sudo.submit') }}
              </v-btn>
            </v-form>

            <template v-else-if="method === 'email_code'">
              <v-btn
                v-if="!codeSentTo"
                block
                color="primary"
                size="large"
                class="sudo__submit"
                :loading="sending"
                @click="sendEmailCode"
              >
                <v-icon start icon="mdi-email-outline" size="20" />
                {{ t('account.sudo.sendEmailCode') }}
              </v-btn>
              <v-form v-else @submit.prevent="verifyEmailCode">
                <p :id="codeLabelId" class="sudo__label">
                  {{ t('account.verifyEmail.sentTo', { email: codeSentTo }) }}
                </p>
                <v-otp-input
                  v-model="code"
                  length="6"
                  type="number"
                  class="sudo__otp"
                  :aria-labelledby="codeLabelId"
                  :disabled="loading"
                  @finish="verifyEmailCode"
                />
                <v-btn
                  block
                  color="primary"
                  size="large"
                  type="submit"
                  class="sudo__submit"
                  :loading="loading"
                  :disabled="code.length !== 6"
                >
                  {{ t('account.sudo.submit') }}
                </v-btn>
                <p class="sudo__resend">
                  <span v-if="resendWait > 0">{{ t('account.verifyEmail.resendIn', { seconds: resendWait }) }}</span>
                  <button v-else type="button" class="sudo__link" :disabled="sending" @click="sendEmailCode">
                    {{ t('account.verifyEmail.resend') }}
                  </button>
                </p>
              </v-form>
            </template>

            <v-form v-else @submit.prevent="verifyTotp">
              <p :id="codeLabelId" class="sudo__label">{{ t('account.twoFactor.totpLede') }}</p>
              <v-otp-input
                v-model="code"
                length="6"
                type="number"
                class="sudo__otp"
                :aria-labelledby="codeLabelId"
                :disabled="loading"
                @finish="verifyTotp"
              />
              <v-btn
                block
                color="primary"
                size="large"
                type="submit"
                class="sudo__submit"
                :loading="loading"
                :disabled="code.length !== 6"
              >
                {{ t('account.sudo.submit') }}
              </v-btn>
            </v-form>
          </div>
        </transition>

        <div v-if="method === primary && alternatives.length" class="sudo__others">
          <p class="sudo__others-label">{{ t('account.sudo.otherMethods') }}</p>
          <button
            v-for="other in alternatives"
            :key="other"
            type="button"
            class="sudo__row"
            :disabled="loading"
            @click="switchTo(other)"
          >
            <v-icon :icon="METHODS[other].icon" size="20" class="sudo__row-icon" />
            <span class="sudo__row-label">{{ t(METHODS[other].label) }}</span>
            <v-icon icon="mdi-chevron-right" size="20" class="sudo__row-chevron" />
          </button>
        </div>
      </template>

      <div class="sudo__actions">
        <v-btn
          v-if="methods !== null && method !== primary"
          variant="text"
          prepend-icon="mdi-chevron-left"
          :disabled="loading"
          @click="switchTo(primary)"
        >
          {{ t('global.back') }}
        </v-btn>
        <v-spacer />
        <v-btn variant="text" @click="cancel">{{ t('account.cancel') }}</v-btn>
      </div>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
/**
 * Confirms who is at the keyboard before a sensitive change, in place, over
 * whatever the person was doing. Mounted once at the app root; `withSudo`
 * opens it and waits for the ticket it gets from the server.
 */
import type { MyAuthMethods } from '@/network/api/users/types'

import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { browserSupportsWebAuthn, startAuthentication } from '@simplewebauthn/browser'

import { pendingSudo } from '@/utils/sudo'

import AccountField from '@/components/account/AccountField.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { currentUserId, currentUserName } from '@/services/account'
import { attemptMessage } from '@/views/account/attemptWait'
import { passkeyWrongHostMessage } from '@/views/account/passkeyHost'

type Method = 'passkey' | 'password' | 'totp' | 'email_code'

const METHODS: Record<Method, { icon: string; label: string }> = {
  passkey: { icon: 'mdi-key-chain', label: 'account.sudo.passkey' },
  password: { icon: 'mdi-form-textbox-password', label: 'account.sudo.method.password' },
  totp: { icon: 'mdi-cellphone-key', label: 'account.sudo.method.totp' },
  email_code: { icon: 'mdi-email-outline', label: 'account.sudo.method.emailCode' },
}

// The server refuses a new code within a minute of the last one.
const RESEND_COOLDOWN_SECONDS = 60

// Say what is being confirmed, so the interruption explains itself.
const ACTIONS: Record<UserApi.SudoPurpose, string> = {
  '2fa:enable': 'account.sudo.action.initTOTP',
  '2fa:disable': 'account.sudo.action.disableTOTP',
  '2fa:backup-codes': 'account.sudo.action.generateBackupCodes',
  '2fa:settings': 'account.sudo.action.update2FASettings',
  'passkey:add': 'account.sudo.action.addPasskey',
  'passkey:delete': 'account.sudo.action.deletePasskey',
  'password:change': 'account.sudo.action.changePassword',
  'oauth:unbind': 'account.sudo.action.unbindOAuthConnection',
  'realname:view': 'account.sudo.action.viewRealName',
  'realname:update': 'account.sudo.action.updateRealName',
}

const titleId = useId()
const codeLabelId = useId()
const webAuthnSupported = browserSupportsWebAuthn()

const request = pendingSudo
const methods = ref<MyAuthMethods | null>(null)
const method = ref<Method>('password')
const loading = ref(false)
const password = ref('')
const code = ref('')
const errorMessage = ref('')
const panel = ref<HTMLElement | null>(null)
/** Where the last email code went, once one has been sent for this request. */
const codeSentTo = ref('')
const codeSentAt = ref(0)
const sending = ref(false)
const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined
const resendWait = computed(() =>
  Math.max(0, RESEND_COOLDOWN_SECONDS - Math.floor((now.value - codeSentAt.value) / 1000))
)

const lede = computed(() =>
  request.value ? t('account.sudo.ledeFor', { action: t(ACTIONS[request.value.purpose]) }) : ''
)

// The strongest way this account has comes first. An account created
// through a third-party sign-in may have no password, and then its email
// may be the only way it has.
const available = computed<Method[]>(() => {
  if (!methods.value) return []
  const list: Method[] = []
  if (webAuthnSupported && methods.value.passkey) list.push('passkey')
  if (methods.value.password) list.push('password')
  if (methods.value.twoFactor) list.push('totp')
  if (methods.value.emailCode) list.push('email_code')
  return list
})
const primary = computed<Method>(() => available.value[0] ?? 'password')
const alternatives = computed(() => available.value.slice(1))

// Where focus was before the dialog took it, to hand it back afterwards.
let returnFocusTo: HTMLElement | null = null

watch(
  request,
  async (current) => {
    if (!current) return
    if (!returnFocusTo && document.activeElement instanceof HTMLElement) returnFocusTo = document.activeElement
    methods.value = null
    loading.value = false
    errorMessage.value = ''
    password.value = ''
    code.value = ''
    codeSentTo.value = ''
    codeSentAt.value = 0
    let found: MyAuthMethods = { password: true, passkey: false, twoFactor: false, emailCode: false }
    try {
      found = (await UserApi.getMyAuthMethods()).data
    } catch {
      // Without the list, offer the password, the way most accounts have.
    }
    if (request.value !== current) return
    methods.value = found
    method.value = primary.value
    await nextTick()
    focusFirst()
  },
  { immediate: true }
)

function switchTo(next: Method) {
  method.value = next
  errorMessage.value = ''
  password.value = ''
  code.value = ''
  // Choosing it from the list is asking for the code.
  if (next === 'email_code' && !codeSentTo.value) sendEmailCode()
}

function focusFirst() {
  const target = panel.value?.querySelector<HTMLElement>(
    'input:not([tabindex="-1"]):not([disabled]), button:not([disabled])'
  )
  target?.focus()
}

function restoreFocus() {
  // Only when nothing else has taken focus since, such as a dialog the
  // confirmed operation opened.
  const lost = !document.activeElement || document.activeElement === document.body
  if (lost && returnFocusTo?.isConnected) returnFocusTo.focus()
  returnFocusTo = null
}

function cancel() {
  request.value?.settle(null)
}

async function sendEmailCode() {
  const current = request.value
  if (!current || sending.value) return
  sending.value = true
  errorMessage.value = ''
  try {
    const { data } = await UserApi.requestSudoEmailCode()
    if (request.value !== current) return
    codeSentTo.value = data.email
    codeSentAt.value = now.value = Date.now()
    clearInterval(ticker)
    ticker = setInterval(() => (now.value = Date.now()), 1000)
  } catch (error: unknown) {
    if (request.value !== current) return
    errorMessage.value = attemptMessage(error) ?? t('account.sudo.sendFailed')
  } finally {
    sending.value = false
  }
}

watch(request, (current) => {
  if (!current) clearInterval(ticker)
})
onBeforeUnmount(() => clearInterval(ticker))

async function verify(run: () => Promise<{ data: { sudoTicket?: string } }>) {
  const current = request.value
  if (!current || loading.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    const { data } = await run()
    if (!data.sudoTicket) throw new Error('No ticket in the confirmation')
    current.settle(data.sudoTicket)
  } catch (error: unknown) {
    if (request.value !== current) return
    // The browser's own WebAuthn error text is English and names internals.
    errorMessage.value =
      (method.value === 'email_code' ? attemptMessage(error) : null) ??
      passkeyWrongHostMessage(error, passkeyRpId) ??
      ((error as { name?: string } | null)?.name === 'NotAllowedError'
        ? t('account.sudo.passkeyCanceled')
        : requestErrorMessage(error, t('account.sudo.failed')))
    code.value = ''
  } finally {
    loading.value = false
  }
}

// The site the server asked the passkey for, to say where it works if this
// address is not covered by it.
let passkeyRpId: string | undefined

function verifyPasskey() {
  const purpose = request.value?.purpose
  return verify(async () => {
    const { data } = await UserApi.getPasskeyAuthenticationOptions(currentUserId.value)
    passkeyRpId = data.options.rpId
    const assertion = await startAuthentication({ optionsJSON: data.options })
    return UserApi.verifySudoPasskey(assertion, purpose)
  })
}

function verifyPassword() {
  if (!password.value) {
    errorMessage.value = t('account.sudo.passwordRequired')
    return
  }
  return verify(() => UserApi.verifySudoPassword(password.value, request.value?.purpose))
}

function verifyTotp() {
  if (code.value.length !== 6) return
  return verify(() => UserApi.verifySudoTOTP(code.value, request.value?.purpose))
}

function verifyEmailCode() {
  if (code.value.length !== 6) return
  return verify(() => UserApi.verifySudoEmailCode(code.value, request.value?.purpose))
}
</script>

<style scoped>
.sudo {
  padding: 24px;
}

.sudo__title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
  color: var(--ink);
}

.sudo__lede {
  margin: 4px 0 24px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
}

.sudo__error {
  margin-bottom: 16px;
}

/* As tall as the primary button it stands in for, so nothing jumps when the
   methods arrive. */
.sudo__loading {
  display: flex;
  align-items: center;
  height: 44px;
}

.sudo__empty {
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.sudo__username {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}

.sudo__label {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 500;
  line-height: var(--lh-13);
  color: var(--text);
}

.sudo__otp {
  padding: 0;
  margin-bottom: 16px;
}

.sudo__resend {
  margin: 12px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--faint);
}

.sudo__link {
  padding: 0;
  font: inherit;
  font-weight: 500;
  color: var(--ink);
  text-decoration: underline;
  text-decoration-color: var(--line-2);
  text-decoration-thickness: 1.5px;
  text-underline-offset: 3px;
  cursor: pointer;
  background: none;
  border: 0;
  transition: text-decoration-color var(--dur-quick) var(--ease-standard);
}

.sudo__link:hover:not(:disabled) {
  text-decoration-color: currentcolor;
}

.sudo__link:disabled {
  color: var(--faint);
  cursor: default;
}

.sudo__link:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

.sudo .v-btn--size-large {
  height: 44px;
}

.sudo__submit {
  font-size: 15px;
}

.sudo__others {
  margin-top: 24px;
}

.sudo__others-label {
  margin: 0 0 4px;
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  color: var(--muted);
}

.sudo__row {
  display: flex;
  gap: 12px;
  align-items: center;
  width: 100%;
  min-height: 44px;
  padding: 0 8px;
  margin: 0 -8px;
  box-sizing: content-box;
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  text-align: start;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.sudo__row:hover:not(:disabled) {
  background: var(--fill);
}

.sudo__row:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}

.sudo__row:disabled {
  color: var(--faint);
  cursor: default;
}

.sudo__row-icon {
  color: var(--muted);
}

.sudo__row-label {
  flex: 1;
}

.sudo__row-chevron {
  color: var(--faint);
}

.sudo__actions {
  display: flex;
  align-items: center;
  margin: 8px -8px -8px;
}

.sudo-method-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}

.sudo-method-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}

.sudo-method-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

.sudo-method-leave-to {
  opacity: 0;
}
</style>
