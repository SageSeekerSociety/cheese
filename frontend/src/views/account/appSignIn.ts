// The browser's side of signing in to the desktop app (AppSignInStart.vue,
// BackToApp.vue). The challenge is kept in this tab only: the provider's pages
// come and go in it, and a sign-in finished in another tab was not asked for
// by the app.

const CHALLENGE_KEY = 'cheese.appChallenge'

/** False for anything that is not a challenge the app could have made. */
export function keepAppChallenge(challenge: string): boolean {
  if (!/^[A-Za-z0-9_-]{43}$/.test(challenge)) return false
  sessionStorage.setItem(CHALLENGE_KEY, challenge)
  return true
}

export function takeAppChallenge(): string | null {
  const challenge = sessionStorage.getItem(CHALLENGE_KEY)
  sessionStorage.removeItem(CHALLENGE_KEY)
  return challenge
}
