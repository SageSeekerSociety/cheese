/**
 * A provider whose callback is registered on another origin of this deployment
 * signs in there, and the sign-in comes back here (lib/desktopApp.ts
 * signInAtOrigin); one registered here signs in here as before.
 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SignIn from './SignIn.vue'

import { UserAuthApi } from '@/api/userAuth'
import { setLocale } from '@/i18n'
import { takeSignInVerifier } from '@/lib/desktopApp'

const OTHER = 'https://other.example'

vi.mock('@/api/userAuth', () => ({
  UserAuthApi: {
    getOAuthProviders: vi.fn(async () => ({
      providers: [
        { id: 'ruc', name: 'RUC', origin: 'https://other.example' },
        { id: 'github', name: 'GitHub', origin: window.location.origin },
      ],
    })),
    redirectToOAuthLogin: vi.fn(),
  },
}))
vi.mock('@/services/account', () => ({ default: { loggedIn: false, login: vi.fn() } }))

let assign: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {})
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  assign.mockRestore()
})

async function mount() {
  const page = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/signin', component: SignIn },
      { path: '/:rest(.*)*', component: page },
    ],
  })
  await router.push('/account/signin?redirect=%2Fprojects')
  await router.isReady()
  return render(SignIn, { global: { plugins: [router, createPinia(), createVuetify({ components, directives })] } })
}

describe('signing in with a provider registered on another origin', () => {
  it('signs in on that origin, to come back here with a secret only this page holds', async () => {
    const view = await mount()

    await fireEvent.click(await view.findByRole('button', { name: /Sign in with RUC/ }))

    await waitFor(() => expect(assign).toHaveBeenCalled())
    const target = new URL(String(assign.mock.calls[0][0]))
    expect(target.origin).toBe(OTHER)
    expect(target.pathname).toBe('/account/oauth/app')
    expect(target.searchParams.get('provider')).toBe('ruc')
    expect(target.searchParams.get('returnTo')).toBe(window.location.origin)
    const kept = takeSignInVerifier()
    expect(kept?.target).toBe('/projects')
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(kept!.verifier)))
    const challenge = btoa(String.fromCharCode(...digest))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '')
    expect(target.searchParams.get('challenge')).toBe(challenge)
    expect(UserAuthApi.redirectToOAuthLogin).not.toHaveBeenCalled()
  })

  it('signs in right here with a provider registered on this origin', async () => {
    const view = await mount()

    await fireEvent.click(await view.findByRole('button', { name: /Sign in with GitHub/ }))

    await waitFor(() => expect(UserAuthApi.redirectToOAuthLogin).toHaveBeenCalled())
    expect(vi.mocked(UserAuthApi.redirectToOAuthLogin).mock.calls[0][0]).toBe('github')
    expect(assign).not.toHaveBeenCalled()
  })
})
