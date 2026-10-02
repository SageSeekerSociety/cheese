// Signing in to the desktop app happens in the browser (lib/desktopApp.ts
// signInInBrowser). In the app, the pages a sign-in starts from send the person
// there. In the browser, AppSignInStart.vue keeps the app's challenge and opens
// the sign-in, sign-up or reset page; once signed in, however that went, the
// tab goes to BackToApp.vue, which asks before handing the sign-in to the app.
//
// The challenge is kept in this tab only: the provider's pages come and go in
// it, and a sign-in finished in another tab was not asked for by the app.

import type { NavigationGuardWithThis } from 'vue-router'
import type { BrowserSignInEntry } from '@/lib/desktopApp'

import { desktopCan } from '@/lib/desktopApp'

const CHALLENGE_KEY = 'cheese.appChallenge'

/** False for anything that is not a challenge the app could have made. */
export function keepAppChallenge(challenge: string): boolean {
  if (!/^[A-Za-z0-9_-]{43}$/.test(challenge)) return false
  sessionStorage.setItem(CHALLENGE_KEY, challenge)
  return true
}

/** The challenge of the sign-in the app asked for in this tab, still waiting to be handed over. */
export function pendingAppChallenge(): string | null {
  return sessionStorage.getItem(CHALLENGE_KEY)
}

/** Used once: after a hand-off, the tab is an ordinary one again. */
export function forgetAppChallenge(): void {
  sessionStorage.removeItem(CHALLENGE_KEY)
}

const ENTRY_OF: Record<string, BrowserSignInEntry> = {
  SignIn: 'signin',
  SignInEmailCode: 'signin',
  SignUpStart: 'signup',
  RecoverPasswordRequest: 'recover',
}

export const ENTRY_PAGE: Record<BrowserSignInEntry, string> = {
  signin: 'SignIn',
  signup: 'SignUpStart',
  recover: 'RecoverPasswordRequest',
}

/** In the app, a page a sign-in starts from leads to the browser instead (DesktopSignIn.vue). */
export const signInHappensInBrowser: NavigationGuardWithThis<undefined> = (to) => {
  if (!desktopCan('links')) return true
  return {
    name: 'DesktopSignIn',
    query: { entry: ENTRY_OF[String(to.name)] ?? 'signin', redirect: to.query.redirect },
  }
}

/**
 * In the browser, someone who signed in for the app — by any way, a sign-up
 * included — is taken to hand the sign-in over rather than into the site.
 */
export function handSignInToApp(signedIn: () => boolean): NavigationGuardWithThis<undefined> {
  return (to) => {
    if (to.path.startsWith('/account') || !pendingAppChallenge() || !signedIn()) return true
    return { name: 'AppSignInHandOff' }
  }
}
