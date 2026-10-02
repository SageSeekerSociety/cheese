// Signing in to the desktop app happens in the browser: in the app, every page a
// sign-in starts from leads there; in the browser, a sign-in made for the app,
// however it went, ends by handing it over rather than in the site.
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import { handSignInToApp, keepAppChallenge, signInHappensInBrowser } from './appSignIn'

type AppWindow = { __TAURI__?: unknown; __CHEESE_APP__?: unknown }
const CHALLENGE = 'a'.repeat(43)
const page = { template: '<div />' }

function routes(guard = true) {
  const enter = guard ? { beforeEnter: signInHappensInBrowser } : {}
  return [
    { path: '/account/signin', name: 'SignIn', component: page, ...enter },
    { path: '/account/signup', name: 'SignUpStart', component: page, ...enter },
    { path: '/account/recover/password', name: 'RecoverPasswordRequest', component: page, ...enter },
    { path: '/account/app', name: 'DesktopSignIn', component: page },
    { path: '/account/oauth/to-app', name: 'AppSignInHandOff', component: page },
    { path: '/:rest(.*)*', component: page },
  ]
}

afterEach(() => {
  delete (window as AppWindow).__TAURI__
  delete (window as AppWindow).__CHEESE_APP__
  sessionStorage.clear()
})

describe('in the app', () => {
  it('sends signing in, signing up and resetting a password to the browser, keeping where it was headed', async () => {
    ;(window as AppWindow).__TAURI__ = { core: { invoke: async () => undefined } }
    ;(window as AppWindow).__CHEESE_APP__ = { can: ['links'] }
    const router = createRouter({ history: createMemoryHistory(), routes: routes() })

    await router.push('/account/signin?redirect=/inbox')
    expect(router.currentRoute.value.name).toBe('DesktopSignIn')
    expect(router.currentRoute.value.query).toEqual({ entry: 'signin', redirect: '/inbox' })

    await router.push('/account/signup')
    expect(router.currentRoute.value.query.entry).toBe('signup')
    await router.push('/account/recover/password')
    expect(router.currentRoute.value.query.entry).toBe('recover')
  })

  it('keeps the old way in an app too old to take links back', async () => {
    ;(window as AppWindow).__TAURI__ = { core: { invoke: async () => undefined } }
    ;(window as AppWindow).__CHEESE_APP__ = { can: [] }
    const router = createRouter({ history: createMemoryHistory(), routes: routes() })
    await router.push('/account/signin')
    expect(router.currentRoute.value.name).toBe('SignIn')
  })
})

describe('in the browser', () => {
  function browser(signedIn: boolean) {
    const router = createRouter({ history: createMemoryHistory(), routes: routes() })
    router.beforeEach(handSignInToApp(() => signedIn))
    return router
  }

  it('shows the sign-in pages as they are', async () => {
    const router = browser(false)
    await router.push('/account/signin')
    expect(router.currentRoute.value.name).toBe('SignIn')
  })

  it('takes someone who signed in for the app to hand it over, wherever the sign-in landed', async () => {
    keepAppChallenge(CHALLENGE)
    const router = browser(true)
    await router.push('/projects/p')
    expect(router.currentRoute.value.name).toBe('AppSignInHandOff')
  })

  it('leaves an ordinary sign-in in the site', async () => {
    const router = browser(true)
    await router.push('/projects/p')
    expect(router.currentRoute.value.path).toBe('/projects/p')
  })

  it('does not interrupt the sign-in pages themselves', async () => {
    keepAppChallenge(CHALLENGE)
    const router = browser(true)
    await router.push('/account/signup')
    expect(router.currentRoute.value.name).toBe('SignUpStart')
  })

  it('keeps nothing that is not a challenge the app could have made', () => {
    expect(keepAppChallenge('short')).toBe(false)
    expect(sessionStorage.length).toBe(0)
  })
})
