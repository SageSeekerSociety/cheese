// The address a sign-in code was last asked for, so the code page survives a
// refresh. Kept for this tab only, and dropped once the sign-in is done.

import { storageGetJson, storageRemove, storageSetJson } from '@/lib/browserStorage'

const KEY = 'cheese.emailCodeSignIn'

export interface PendingCode {
  email: string
  /** When the code was asked for (ms since epoch). */
  sentAt: number
}

export function rememberCodeSent(email: string): void {
  storageSetJson(KEY, { email, sentAt: Date.now() }, 'session')
}

export function pendingCode(): PendingCode | null {
  const raw = storageGetJson<{ email?: unknown; sentAt?: unknown }>(KEY, 'session')
  if (raw && typeof raw.email === 'string' && raw.email && typeof raw.sentAt === 'number') {
    return { email: raw.email, sentAt: raw.sentAt }
  }
  return null
}

export function forgetPendingCode(): void {
  storageRemove(KEY, 'session')
}
