// How this browser last signed in, so the sign-in page can put that way first.
// Kept on the device only: it is a convenience, and clearing site data simply
// brings back the default order.

export type SignInMethod = 'password' | 'passkey' | `oauth:${string}`

const KEY = 'cheese.lastSignIn'

export function rememberSignIn(method: SignInMethod): void {
  try {
    localStorage.setItem(KEY, method)
  } catch {
    // storage unavailable — the page keeps its default order
  }
}

export function lastSignIn(): SignInMethod | null {
  try {
    const value = localStorage.getItem(KEY)
    if (value === 'password' || value === 'passkey' || value?.startsWith('oauth:')) return value as SignInMethod
  } catch {
    // as above
  }
  return null
}
