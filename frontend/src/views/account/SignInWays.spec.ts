/** The way this browser last signed in goes first, and says so. */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SignIn from './SignIn.vue'

import { setLocale } from '@/i18n'

vi.mock('@/network/api/users', () => ({
  UserApi: {
    getOAuthProviders: vi.fn().mockResolvedValue({
      data: {
        providers: [
          { id: 'github', name: 'GitHub' },
          { id: 'google', name: 'Google' },
        ],
      },
    }),
  },
}))
vi.mock('@/services/account', () => ({ default: { loggedIn: false, login: vi.fn() } }))

beforeEach(() => {
  localStorage.clear()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function mount() {
  const page = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/signin', component: SignIn },
      { path: '/legal/terms', name: 'LegalTerms', component: page },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: page },
      { path: '/:rest(.*)*', component: page },
    ],
  })
  await router.push('/account/signin')
  await router.isReady()
  return render(SignIn, { global: { plugins: [router, createPinia(), createVuetify({ components, directives })] } })
}

const precedes = (a: Element, b: Element) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

describe('signing in the way used last time', () => {
  it('puts the provider used last time first and marks it', async () => {
    localStorage.setItem('cheese.lastSignIn', 'oauth:google')
    const view = await mount()

    const google = await view.findByRole('button', { name: /Sign in with Google/ })
    const github = view.getByRole('button', { name: /Sign in with GitHub/ })
    expect(precedes(google, github)).toBe(true)
    expect(google.textContent).toContain('Last used')
    expect(github.textContent).not.toContain('Last used')
  })

  it('puts the password form first when a password was used last time', async () => {
    localStorage.setItem('cheese.lastSignIn', 'password')
    const view = await mount()

    const github = await view.findByRole('button', { name: /Sign in with GitHub/ })
    expect(precedes(view.getByLabelText('Username'), github)).toBe(true)
  })

  it('offers a mailed code, marked when it was used last time', async () => {
    localStorage.setItem('cheese.lastSignIn', 'email_code')
    const view = await mount()

    const byEmail = await view.findByRole('button', { name: /Sign in with an email code/ })
    const github = await view.findByRole('button', { name: /Sign in with GitHub/ })
    expect(precedes(byEmail, github)).toBe(true)
    expect(byEmail.textContent).toContain('Last used')
  })

  it('leads with the one-click ways when nothing was used before', async () => {
    const view = await mount()

    const github = await view.findByRole('button', { name: /Sign in with GitHub/ })
    expect(precedes(github, view.getByLabelText('Username'))).toBe(true)
    expect(view.queryByText('Last used')).toBeNull()
  })
})
