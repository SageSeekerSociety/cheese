// 敏感操作前确认身份（`utils/sudo.ts` 的 `withSudo` 打开它）：和后端怎么谈这件事
// 收在这里，`components/account/SudoDialog.vue` 只画那个弹窗、只管焦点。
//
// 为什么不走 props + emit：这不是「把一份数据画出来」，是一段有状态的多步对话——
// 先问这台账号有哪些方式，再按方式换一张一次性票据；通行密钥还要先拿一次挑战、让
// 设备签了再交回去。把密码、验证码和这些中间状态抬到应用外壳上，只是让 App.vue
// 拿住一个一次性的密码框。线因此划在组件与这个 composable 之间：组件测试可以换成
// 一段假的对话，不必拿模块 mock 去截网络层。
import type { Ref } from 'vue'
import type { MyAuthMethods } from '@/network/api/users/types'
import type { SudoRequest } from '@/utils/sudo'

import { computed, onScopeDispose, ref, watch } from 'vue'

import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { currentUserId, currentUserName } from '@/services/account'
import { attemptMessage } from '@/views/account/attemptWait'
import { passkeyWrongHostMessage } from '@/views/account/passkeyHost'

/** 弹窗里能挑的几路确认；报错文案按路数分（见 `verify`）。 */
export type SudoMethod = 'passkey' | 'password' | 'totp' | 'email_code'

// The server refuses a new code within a minute of the last one.
const RESEND_COOLDOWN_SECONDS = 60

// 一份方式都问不到时按最常见的账号来：有密码。
const FALLBACK_METHODS: MyAuthMethods = {
  password: true,
  passkey: false,
  twoFactor: false,
  emailCode: false,
}

export function useSudoChallenge(request: Ref<SudoRequest | null>) {
  const methods = ref<MyAuthMethods | null>(null)
  const loading = ref(false)
  const errorMessage = ref('')
  const password = ref('')
  const code = ref('')
  /** Where the last email code went, once one has been sent for this request. */
  const codeSentTo = ref('')
  const codeSentAt = ref(0)
  const sending = ref(false)
  const now = ref(Date.now())
  let ticker: ReturnType<typeof setInterval> | undefined
  const resendWait = computed(() =>
    Math.max(0, RESEND_COOLDOWN_SECONDS - Math.floor((now.value - codeSentAt.value) / 1000))
  )
  // The site the server asked the passkey for, to say where it works if this
  // address is not covered by it.
  let passkeyRpId: string | undefined

  /** 新的一次确认：把上一次留下的输入和提示一起清掉，再问这台账号有哪些方式。 */
  async function loadMethods(): Promise<void> {
    const current = request.value
    if (!current) return
    methods.value = null
    loading.value = false
    errorMessage.value = ''
    password.value = ''
    code.value = ''
    codeSentTo.value = ''
    codeSentAt.value = 0
    let found = FALLBACK_METHODS
    try {
      found = (await UserApi.getMyAuthMethods()).data
    } catch {
      // Without the list, offer the password, the way most accounts have.
    }
    if (request.value !== current) return
    methods.value = found
  }

  // An email code stops being accepted once two-step verification is on, which
  // can happen in another tab while this dialog is open.
  function emailCodeMessage(error: unknown): string | null {
    const reason = (error as { error?: { data?: { reason?: unknown } } } | null)?.error?.data?.reason
    if (reason === 'email_code_unavailable') return t('account.sudo.emailCodeUnavailable')
    return attemptMessage(error)
  }

  async function sendEmailCode(): Promise<void> {
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
      errorMessage.value = emailCodeMessage(error) ?? t('account.sudo.sendFailed')
    } finally {
      sending.value = false
    }
  }

  /** 换票：跑一段、拿回票据就交给等着的调用方；失败就落一条说得清的提示。 */
  async function verify(kind: SudoMethod, run: () => Promise<{ data: { sudoTicket?: string } }>): Promise<void> {
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
        (kind === 'email_code' ? emailCodeMessage(error) : null) ??
        passkeyWrongHostMessage(error, passkeyRpId) ??
        ((error as { name?: string } | null)?.name === 'NotAllowedError'
          ? t('account.sudo.passkeyCanceled')
          : requestErrorMessage(error, t('account.sudo.failed')))
      code.value = ''
    } finally {
      loading.value = false
    }
  }

  function verifyPassword(): Promise<void> {
    if (!password.value) {
      errorMessage.value = t('account.sudo.passwordRequired')
      return Promise.resolve()
    }
    return verify('password', () => UserApi.verifySudoPassword(password.value, request.value?.purpose))
  }

  function verifyTotp(): Promise<void> {
    if (code.value.length !== 6) return Promise.resolve()
    return verify('totp', () => UserApi.verifySudoTOTP(code.value, request.value?.purpose))
  }

  function verifyEmailCode(): Promise<void> {
    if (code.value.length !== 6) return Promise.resolve()
    return verify('email_code', () => UserApi.verifySudoEmailCode(code.value, request.value?.purpose))
  }

  /**
   * 通行密钥那一路：先拿挑战，再让这台设备签，最后把签名交回服务端。
   * `sign` 由调用方给——那是浏览器的 `startAuthentication`，不是服务端的事；
   * 它抛的错要和别的错走同一套文案，所以整段都在 `verify` 里面跑。
   */
  function verifyPasskey(sign: (options: any) => Promise<unknown>): Promise<void> {
    return verify('passkey', async () => {
      const { data } = await UserApi.getPasskeyAuthenticationOptions(currentUserId.value)
      passkeyRpId = data.options.rpId
      const assertion = await sign(data.options)
      return UserApi.verifySudoPasskey(assertion, request.value?.purpose)
    })
  }

  watch(request, (current) => {
    if (!current) clearInterval(ticker)
  })
  onScopeDispose(() => clearInterval(ticker))

  return {
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
    verifyPasskey,
  }
}
