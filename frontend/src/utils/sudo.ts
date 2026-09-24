import { shallowRef } from 'vue'

import { UserApi } from '@/network/api/users'

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
 * A ticket the server hands out without asking, while this sign-in is still
 * inside the few minutes after it last proved who is at it; null when the
 * server says the person has to confirm again.
 */
async function ticketWithoutAsking(purpose: UserApi.SudoPurpose): Promise<string | null> {
  try {
    const { data } = await UserApi.requestSudoTicket(purpose)
    return data.sudoTicket ?? null
  } catch (error) {
    if ((error as { name?: unknown } | null)?.name === 'SudoRequiredError') return null
    throw error
  }
}

/**
 * Run an operation the server gates on a sudo ticket. Shortly after the person
 * signed in or confirmed who they are, the server grants the ticket without
 * asking; otherwise they confirm in a dialog over the current page. The
 * operation then runs with the single-use ticket the server signed for
 * `purpose`, and its result comes back here. Rejects with SudoCancelledError,
 * without running it, when they back out.
 */
export async function withSudo<T>(
  purpose: UserApi.SudoPurpose,
  operation: (sudoTicket: string) => Promise<T>
): Promise<T> {
  return operation((await ticketWithoutAsking(purpose)) ?? (await confirmIdentity(purpose)))
}
