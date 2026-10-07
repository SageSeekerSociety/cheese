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

      <BaseEmptyState v-else-if="!available.length" size="compact" :title="t('account.sudo.noMethod')" />

      <template v-else>
        <transition name="sudo-method" mode="out-in" @after-enter="focusFirst">
          <div :key="method" ref="panel">
            <BaseButton
              v-if="method === 'passkey'"
              kind="primary"
              size="lg"
              block
              prepend-icon="mdi-key-chain"
              :loading="loading"
              @click="verifyPasskey"
            >
              {{ t('account.sudo.passkey') }}
            </BaseButton>

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
              <BaseButton kind="primary" size="lg" block type="submit" :loading="loading">
                {{ t('account.sudo.submit') }}
              </BaseButton>
            </v-form>

            <template v-else-if="method === 'email_code'">
              <BaseButton
                v-if="!codeSentTo"
                kind="primary"
                size="lg"
                block
                prepend-icon="mdi-email-outline"
                :loading="sending"
                @click="sendEmailCode"
              >
                {{ t('account.sudo.sendEmailCode') }}
              </BaseButton>
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
                <BaseButton
                  kind="primary"
                  size="lg"
                  block
                  type="submit"
                  :loading="loading"
                  :disabled="code.length !== 6"
                >
                  {{ t('account.sudo.submit') }}
                </BaseButton>
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
              <BaseButton kind="primary" size="lg" block type="submit" :loading="loading" :disabled="code.length !== 6">
                {{ t('account.sudo.submit') }}
              </BaseButton>
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
        <BaseButton
          v-if="methods !== null && method !== primary"
          kind="ghost"
          prepend-icon="mdi-chevron-left"
          :disabled="loading"
          @click="switchTo(primary)"
        >
          {{ t('global.back') }}
        </BaseButton>
        <v-spacer />
        <BaseButton kind="ghost" @click="cancel">{{ t('account.cancel') }}</BaseButton>
      </div>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
/**
 * Confirms who is at the keyboard before a sensitive change, in place, over
 * whatever the person was doing. Mounted once at the app root; `withSudo`
 * opens it and waits for the ticket it gets from the server.
 *
 * 和后端怎么谈在 `useSudoChallenge` 里（方式清单、验证码、换票），这里只管画、
 * 管焦点、管挑哪一路——组件因此不吃 API 层（.claude/rules/architecture.md）。
 */
import type { SudoMethod } from '@/composables/useSudoChallenge'
import type { SudoRequest } from '@/utils/sudo'

import { computed, nextTick, ref, useId, watch } from 'vue'
import { browserSupportsWebAuthn, startAuthentication } from '@simplewebauthn/browser'

import { pendingSudo } from '@/utils/sudo'

import { useSudoChallenge } from '@/composables/useSudoChallenge'

import AccountField from '@/components/account/AccountField.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'

type Method = SudoMethod

const METHODS: Record<Method, { icon: string; label: string }> = {
  passkey: { icon: 'mdi-key-chain', label: 'account.sudo.passkey' },
  password: { icon: 'mdi-form-textbox-password', label: 'account.sudo.method.password' },
  totp: { icon: 'mdi-cellphone-key', label: 'account.sudo.method.totp' },
  email_code: { icon: 'mdi-email-outline', label: 'account.sudo.method.emailCode' },
}

// Say what is being confirmed, so the interruption explains itself.
const ACTIONS: Record<SudoRequest['purpose'], string> = {
  '2fa:enable': 'account.sudo.action.initTOTP',
  '2fa:disable': 'account.sudo.action.disableTOTP',
  '2fa:backup-codes': 'account.sudo.action.generateBackupCodes',
  'passkey:add': 'account.sudo.action.addPasskey',
  'passkey:delete': 'account.sudo.action.deletePasskey',
  'password:change': 'account.sudo.action.changePassword',
  'oauth:unbind': 'account.sudo.action.unbindOAuthConnection',
  'realname:view': 'account.sudo.action.viewRealName',
  'realname:update': 'account.sudo.action.updateRealName',
  'realname:delete': 'account.sudo.action.deleteRealName',
}

const titleId = useId()
const codeLabelId = useId()
const webAuthnSupported = browserSupportsWebAuthn()

const request = pendingSudo
const {
  methods,
  loading,
  errorMessage,
  password,
  code,
  codeSentTo,
  sending,
  resendWait,
  currentUserName,
  loadMethods,
  sendEmailCode,
  verifyPassword,
  verifyTotp,
  verifyEmailCode,
  verifyPasskey: confirmWithPasskey,
} = useSudoChallenge(request)

/** 哪一路在台面上；挑它是界面的事，用什么换票是 composable 的事。 */
const method = ref<Method>('password')
const panel = ref<HTMLElement | null>(null)

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
    await loadMethods()
    if (request.value !== current) return
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

/** 通行密钥：设备那一步用浏览器的 WebAuthn，换票交给 composable。 */
function verifyPasskey() {
  return confirmWithPasskey((options) => startAuthentication({ optionsJSON: options }))
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
