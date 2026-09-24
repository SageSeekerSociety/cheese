<template>
  <div>
    <AccountHeading :title="t('account.sudo.title')" :lede="lede" />

    <v-alert v-if="errorMessage" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorMessage }}
    </v-alert>

    <v-progress-linear v-if="isInitializing" indeterminate color="primary" height="2" />

    <template v-else>
      <transition name="sudo-method" mode="out-in">
        <div :key="activeMethod">
          <v-btn
            v-if="activeMethod === 'passkey'"
            block
            color="primary"
            size="large"
            class="account-submit"
            :loading="loading"
            @click="handlePasskeyVerify"
          >
            <v-icon start icon="mdi-key-chain" size="20" />
            {{ t('account.sudo.passkey') }}
          </v-btn>

          <v-form v-else-if="activeMethod === 'password'" @submit.prevent="handlePasswordVerify">
            <AccountField :label="t('account.field.password')" input-id="sudo-password">
              <PasswordField
                id="sudo-password"
                v-model="password"
                autocomplete="current-password"
                name="password"
                autofocus
              />
            </AccountField>
            <v-btn block color="primary" size="large" type="submit" class="account-submit" :loading="loading">
              {{ t('account.sudo.submit') }}
            </v-btn>
          </v-form>

          <v-form v-else @submit.prevent="handleTOTPVerify">
            <v-otp-input v-model="totpCode" length="6" type="number" class="account-otp" @finish="handleTOTPVerify" />
            <v-btn
              block
              color="primary"
              size="large"
              type="submit"
              class="account-submit"
              :loading="loading"
              :disabled="totpCode.length !== 6"
            >
              {{ t('account.sudo.submit') }}
            </v-btn>
          </v-form>
        </div>
      </transition>

      <div class="account-foot account-foot--split">
        <span v-if="otherMethods.length" class="sudo-others">
          <button
            v-for="method in otherMethods"
            :key="method.id"
            type="button"
            class="account-link"
            :disabled="loading"
            @click="switchTo(method.id)"
          >
            {{ method.label }}
          </button>
        </span>
        <button type="button" class="account-link account-link--quiet" @click="cancel">
          {{ t('account.cancel') }}
        </button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { browserSupportsWebAuthn, startAuthentication } from '@simplewebauthn/browser'

import { sudoPurposeFor } from '@/utils/sudo'

import { passkeyWrongHostMessage } from './passkeyHost'

import AccountField from '@/components/account/AccountField.vue'
import AccountHeading from '@/components/account/AccountHeading.vue'
import PasswordField from '@/components/account/PasswordField.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { currentUserId, currentUserName } from '@/services/account'
import { useSudoStore } from '@/stores/sudo'

type Method = 'passkey' | 'password' | 'totp'

const router = useRouter()
const sudoStore = useSudoStore()

// 这次验证是为哪一件事做的。服务端把它签进票里，所以为「添加通行密钥」验的
// 那一次，换不来一张能关掉两步验证的票。
const opKey = computed(() => sudoStore.retryOperation?.opKey)
const purpose = computed(() => sudoPurposeFor(opKey.value))

// Say what is being confirmed, so the interruption explains itself.
const ACTIONS: Record<string, string> = {
  initTOTP: 'account.sudo.action.initTOTP',
  disableTOTP: 'account.sudo.action.disableTOTP',
  generateBackupCodes: 'account.sudo.action.generateBackupCodes',
  update2FASettings: 'account.sudo.action.update2FASettings',
  addPasskey: 'account.sudo.action.addPasskey',
  deletePasskey: 'account.sudo.action.deletePasskey',
  changePassword: 'account.sudo.action.changePassword',
  unbindOAuthConnection: 'account.sudo.action.unbindOAuthConnection',
}
const lede = computed(() => {
  const key = opKey.value && ACTIONS[opKey.value]
  return key ? t('account.sudo.ledeFor', { action: t(key) }) : t('account.sudo.lede')
})

const activeMethod = ref<Method>('password')
const loading = ref(false)
const password = ref('')
const totpCode = ref('')
const errorMessage = ref('')
const isInitializing = ref(true)
const webAuthnSupported = browserSupportsWebAuthn()
const authMethods = ref({ supports_passkey: false, supports_2fa: false })

const METHOD_LABELS: Record<Method, string> = {
  passkey: 'account.sudo.usePasskey',
  password: 'account.sudo.usePassword',
  totp: 'account.sudo.useTotp',
}

const available = computed<Method[]>(() => {
  const list: Method[] = []
  if (webAuthnSupported && authMethods.value.supports_passkey) list.push('passkey')
  if (authMethods.value.supports_2fa) list.push('totp')
  list.push('password')
  return list
})

const otherMethods = computed(() =>
  available.value.filter((m) => m !== activeMethod.value).map((id) => ({ id, label: t(METHOD_LABELS[id]) }))
)

function switchTo(method: Method) {
  activeMethod.value = method
  errorMessage.value = ''
  password.value = ''
  totpCode.value = ''
}

async function verify(run: () => Promise<{ data: { sudoTicket?: string } }>) {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await run()
    // 把服务端签的票交给待重试的操作，回到原来的页面
    sudoStore.setVerified(response.data.sudoTicket)
    router.replace(sudoStore.returnPath || '/')
  } catch (error: any) {
    // The browser's own WebAuthn error text is English and names internals.
    errorMessage.value =
      passkeyWrongHostMessage(error, passkeyRpId) ??
      (error?.name === 'NotAllowedError'
        ? t('account.sudo.passkeyCanceled')
        : requestErrorMessage(error, t('account.sudo.failed')))
    totpCode.value = ''
  } finally {
    loading.value = false
  }
}

// The site the server asked the passkey for, to say where it works if this
// address is not covered by it.
let passkeyRpId: string | undefined

const handlePasskeyVerify = () =>
  verify(async () => {
    const { data } = await UserApi.getPasskeyAuthenticationOptions(currentUserId.value)
    passkeyRpId = data.options.rpId
    const assertion = await startAuthentication({ optionsJSON: data.options })
    return UserApi.verifySudoPasskey(assertion, purpose.value)
  })

const handlePasswordVerify = () => {
  if (!password.value) {
    errorMessage.value = t('account.sudo.passwordRequired')
    return
  }
  verify(() => UserApi.verifySudoPassword(password.value, purpose.value))
}

const handleTOTPVerify = () => {
  if (loading.value || totpCode.value.length !== 6) return
  verify(() => UserApi.verifySudoTOTP(totpCode.value, purpose.value))
}

// Backing out drops the pending operation, so nothing retries behind the
// person's back later.
function cancel() {
  const back = sudoStore.returnPath || '/'
  sudoStore.clearRetryState()
  router.replace(back)
}

onMounted(async () => {
  try {
    if (currentUserName.value) {
      const { data } = await UserApi.getAuthMethods(currentUserName.value)
      authMethods.value = data
    }
  } catch {
    // Without the list, the password is the way every account has.
  } finally {
    activeMethod.value = available.value[0]
    isInitializing.value = false
  }
})
</script>

<style scoped>
.sudo-others {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
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
