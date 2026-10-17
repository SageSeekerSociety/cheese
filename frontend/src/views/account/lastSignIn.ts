// How this browser last signed in, so the sign-in page can put that way first.
// Kept on the device only: it is a convenience, and clearing site data simply
// brings back the default order.

import { storageGet, storageSet } from '@/lib/browserStorage'

export type SignInMethod = 'password' | 'passkey' | 'email_code' | `oauth:${string}`

const KEY = 'cheese.lastSignIn'

export function rememberSignIn(method: SignInMethod): void {
  storageSet(KEY, method)
}

export function lastSignIn(): SignInMethod | null {
  const value = storageGet(KEY)
  if (value === 'password' || value === 'passkey' || value === 'email_code' || value?.startsWith('oauth:'))
    return value as SignInMethod
  return null
}
