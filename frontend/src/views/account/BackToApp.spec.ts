// In the browser, the end of a sign-in the desktop app asked for. Any program on
// the computer can open this page with a challenge of its own, so nothing is
// handed over until the person, shown the account, says so.
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { keepAppChallenge, pendingAppChallenge } from './appSignIn'
import BackToApp from './BackToApp.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'

vi.mock('@/network/api/users', () => ({
  UserApi: { startAppSignIn: vi.fn(async () => ({ data: { code: 'the-code' } })) },
}))
vi.mock('@/services/account', () => ({
  default: { user: { id: 7, username: 'andylizf', nickname: 'Andy' }, logout: vi.fn(async () => {}) },
}))

const CHALLENGE = 'b'.repeat(43)

beforeEach(() => {
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  localStorage.setItem('user', JSON.stringify({ id: 7, username: 'andylizf' }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
})

async function arrive() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/oauth/to-app', component: BackToApp, props: { signIn: true } },
      { path: '/account/signin', name: 'SignIn', component: { template: '<div />' } },
      { path: '/:rest(.*)*', component: { template: '<div />' } },
    ],
  })
  await router.push('/account/oauth/to-app')
  await router.isReady()
  const view = render(BackToApp, {
    props: { signIn: true },
    global: { plugins: [router, createVuetify({ components, directives })] },
  })
  return { router, view }
}

describe('handing a browser sign-in to the app', () => {
  it('names the account and hands nothing over before the person continues', async () => {
    keepAppChallenge(CHALLENGE)
    const { view } = await arrive()

    await waitFor(() => expect(view.getByText(/as Andy \(andylizf\)/)).toBeTruthy())
    expect(UserApi.startAppSignIn).not.toHaveBeenCalled()

    await fireEvent.click(view.getByRole('button', { name: 'Continue' }))
    await waitFor(() => expect(UserApi.startAppSignIn).toHaveBeenCalledWith(CHALLENGE))
    await waitFor(() =>
      expect(view.getByRole('link', { name: 'Open Cheese' }).getAttribute('href')).toContain('cheese://open')
    )
    expect(pendingAppChallenge()).toBeNull()
  })

  it('hands nothing over once the person cancels, and the tab is an ordinary one again', async () => {
    keepAppChallenge(CHALLENGE)
    const { router, view } = await arrive()

    await fireEvent.click(await view.findByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(router.currentRoute.value.path).toBe('/'))
    expect(UserApi.startAppSignIn).not.toHaveBeenCalled()
    expect(pendingAppChallenge()).toBeNull()
  })

  it('goes nowhere near the app for a sign-in the app did not ask for in this tab', async () => {
    const { router } = await arrive()
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/'))
    expect(UserApi.startAppSignIn).not.toHaveBeenCalled()
  })
})
