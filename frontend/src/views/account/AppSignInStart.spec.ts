// In the browser, where a sign-in the desktop app asked for starts.
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { pendingAppChallenge, pendingReturnOrigin } from './appSignIn'
import AppSignInStart from './AppSignInStart.vue'
import { inviteCodeFor } from './inviteCode'

import { UserAuthApi } from '@/api/userAuth'

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

  it("keeps the friend's invite code in this tab for the sign-up page", async () => {
    const router = await start(`entry=signup&challenge=${CHALLENGE}&invite=K7Q2M9`)
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('SignUpStart'))
    expect(inviteCodeFor(undefined)).toBe('K7Q2M9')
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

  it('goes straight to the provider for a sign-in another origin of this deployment started', async () => {
    const toProvider = vi.spyOn(UserAuthApi, 'redirectToOAuthLogin').mockImplementation(() => {})
    // Signed in here already: the sign-in asked for is the provider's all the same.
    localStorage.setItem('user', JSON.stringify({ id: 7, username: 'andylizf' }))
    const router = await start(`entry=signin&challenge=${CHALLENGE}&provider=ruc&returnTo=https%3A%2F%2Falias.example`)

    await vi.waitFor(() => expect(toProvider).toHaveBeenCalledWith('ruc'))
    expect(pendingAppChallenge()).toBe(CHALLENGE)
    expect(pendingReturnOrigin()).toBe('https://alias.example')
    expect(router.currentRoute.value.name).not.toBe('AppSignInHandOff')
    toProvider.mockRestore()
  })
})
