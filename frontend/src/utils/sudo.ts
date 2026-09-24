import type { Router } from 'vue-router'
import type { UserApi } from '@/network/api/users'

import { shallowRef } from 'vue'

import { SudoRequiredError } from '@/network/types/error'
import { useSudoStore } from '@/stores/sudo'

/**
 * The person closed the confirmation instead of completing it. Nothing ran,
 * and nothing went wrong, so callers let it pass without a message.
 */
export class SudoCancelledError extends Error {
  constructor() {
    super('Identity confirmation was cancelled')
    this.name = 'SudoCancelledError'
  }
}

export type SudoRequest = {
  purpose: UserApi.SudoPurpose
  /** A ticket completes the request; `null` cancels it. Only the first call counts. */
  settle: (ticket: string | null) => void
}

/** The confirmation the identity dialog is showing, if any. */
export const pendingSudo = shallowRef<SudoRequest | null>(null)

/**
 * Ask the person to confirm who they are, and resolve with the single-use
 * ticket the server signs for `purpose`. Rejects with SudoCancelledError when
 * they back out.
 */
export function confirmIdentity(purpose: UserApi.SudoPurpose): Promise<string> {
  // One dialog at a time. A newer request takes its place, and the older one
  // ends as cancelled rather than waiting on a dialog nobody can see.
  pendingSudo.value?.settle(null)
  return new Promise((resolve, reject) => {
    const request: SudoRequest = {
      purpose,
      settle(ticket) {
        if (pendingSudo.value !== request) return
        pendingSudo.value = null
        if (ticket) resolve(ticket)
        else reject(new SudoCancelledError())
      },
    }
    pendingSudo.value = request
  })
}

// 存储最后一次需要重试的操作
let lastOperation: (() => Promise<any>) | null = null

// 服务端会验票的操作，票上写着它是为哪一件事签的。没列在这里的操作服务端
// 不收票，也就不该去要一张——一张没人验的票不是保护，是多配了一把钥匙。
const SUDO_PURPOSES: Record<string, UserApi.SudoPurpose> = {
  initTOTP: '2fa:enable',
  disableTOTP: '2fa:disable',
  generateBackupCodes: '2fa:backup-codes',
  update2FASettings: '2fa:settings',
  addPasskey: 'passkey:add',
  deletePasskey: 'passkey:delete',
  changePassword: 'password:change',
  unbindOAuthConnection: 'oauth:unbind',
}

export function sudoPurposeFor(opKey: string | undefined | null): UserApi.SudoPurpose | undefined {
  return opKey ? SUDO_PURPOSES[opKey] : undefined
}

export async function withSudo<T>(
  operation: (sudoTicket: string) => Promise<T>,
  opKey: string,
  opData: any,
  router: Router
): Promise<T> {
  const sudoStore = useSudoStore()

  try {
    // 第一次进来手里是空的，操作会被服务端以 SudoRequiredError 挡回来；
    // 验证完再跑一遍时，票就在 store 里等着。
    return await operation(sudoStore.consumeTicket() ?? '')
  } catch (error) {
    if (error instanceof SudoRequiredError) {
      // 存储待重试的操作标识及数据，而非直接存储函数
      sudoStore.setRetryOperation({
        opKey,
        opData,
        returnPath: router.currentRoute.value.fullPath,
      })
      // 跳转到 sudo 验证页面
      router.push('/account/sudo-verify')
      // 返回一个永远不会 resolve 的 promise
      return new Promise(() => {})
    }
    throw error
  }
}

// 检查是否需要重试
export function checkSudoRetry() {
  const sudoStore = useSudoStore()
  return sudoStore.needsRetry
}

// 清除重试状态
export function clearSudoRetry() {
  const sudoStore = useSudoStore()
  sudoStore.clearRetryState()
}

// 获取并清除最后保存的操作
export function getAndClearLastOperation() {
  const operation = lastOperation
  lastOperation = null
  return operation
}
