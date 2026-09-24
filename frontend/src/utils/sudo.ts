import type { UserApi } from '@/network/api/users'

import { shallowRef } from 'vue'

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

function confirmIdentity(purpose: UserApi.SudoPurpose): Promise<string> {
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

/**
 * Run an operation the server gates on a sudo ticket. The person confirms who
 * they are in a dialog over the current page; the operation then runs with the
 * single-use ticket the server signed for `purpose`, and its result comes back
 * here. Rejects with SudoCancelledError, without running it, when they back out.
 */
export async function withSudo<T>(
  purpose: UserApi.SudoPurpose,
  operation: (sudoTicket: string) => Promise<T>
): Promise<T> {
  return operation(await confirmIdentity(purpose))
}
