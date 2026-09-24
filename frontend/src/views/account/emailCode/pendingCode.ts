// The address a sign-in code was last asked for, so the code page survives a
// refresh. Kept for this tab only, and dropped once the sign-in is done.

const KEY = 'cheese.emailCodeSignIn'

export interface PendingCode {
  email: string
  /** When the code was asked for (ms since epoch). */
  sentAt: number
}

export function rememberCodeSent(email: string): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ email, sentAt: Date.now() }))
  } catch {
    // storage unavailable — a refresh sends the person back to enter the address
  }
}

export function pendingCode(): PendingCode | null {
  try {
    const raw = JSON.parse(sessionStorage.getItem(KEY) ?? 'null')
    if (raw && typeof raw.email === 'string' && raw.email && typeof raw.sentAt === 'number') {
      return { email: raw.email, sentAt: raw.sentAt }
    }
  } catch {
    // as above
  }
  return null
}

export function forgetPendingCode(): void {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    // nothing was kept
  }
}
