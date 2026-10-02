// In the browser, where a sign-in the desktop app asked for starts.
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { pendingAppChallenge } from './appSignIn'
import AppSignInStart from './AppSignInStart.vue'

const CHALLENGE = 'c'.repeat(43)
const page = { template: '<div />' }

afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
})

async function start(query: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/oauth/app', component: AppSignInStart },
      { path: '/account/signin', name: 'SignIn', component: page },
      { path: '/account/signup', name: 'SignUpStart', component: page },
      { path: '/account/recover/password', name: 'RecoverPasswordRequest', component: page },
      { path: '/account/oauth/to-app', name: 'AppSignInHandOff', component: page },
    ],
  })
  await router.push(`/account/oauth/app?${query}`)
  render(AppSignInStart, { global: { plugins: [router, createVuetify({ components })] } })
  return router
}

describe('starting an app sign-in in the browser', () => {
  it('opens the page the app asked for, keeping its challenge in this tab', async () => {
    const router = await start(`entry=signup&challenge=${CHALLENGE}`)
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('SignUpStart'))
    expect(pendingAppChallenge()).toBe(CHALLENGE)
  })

  it('goes straight to handing over for someone already signed in here', async () => {
    localStorage.setItem('user', JSON.stringify({ id: 7, username: 'andylizf' }))
    const router = await start(`entry=signin&challenge=${CHALLENGE}`)
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('AppSignInHandOff'))
  })

  it('is an ordinary sign-in when the challenge is not one the app could have made', async () => {
    const router = await start('entry=signup&challenge=nope')
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('SignIn'))
    expect(pendingAppChallenge()).toBeNull()
  })
})
