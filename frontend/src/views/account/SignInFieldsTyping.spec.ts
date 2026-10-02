// A username or an email is typed exactly as it is: the desktop app's web
// view, like a phone's, would otherwise capitalize the first letter and
// "correct" the rest, and the sign-in then fails for no reason the person sees.
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
  UserApi: { getOAuthProviders: vi.fn().mockResolvedValue({ data: { providers: [] } }) },
}))
vi.mock('@/services/account', () => ({ default: { loggedIn: false, login: vi.fn() } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => {
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function openSignIn() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/signin', component: SignIn },
      { path: '/legal/terms', name: 'LegalTerms', component: { template: '<div />' } },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: { template: '<div />' } },
    ],
  })
  await router.push('/account/signin')
  await router.isReady()
  return render(SignIn, { global: { plugins: [router, createPinia(), createVuetify({ components, directives })] } })
}

describe('sign-in fields', () => {
  it('take the username as typed, with no capital letter or correction added', async () => {
    const input = (await openSignIn()).getByLabelText('Username')
    expect(input.getAttribute('autocapitalize')).toBe('none')
    expect(input.getAttribute('autocorrect')).toBe('off')
    expect(input.getAttribute('spellcheck')).toBe('false')
    expect(input.getAttribute('autocomplete')).toBe('username webauthn')
  })

  it('take the password as typed, even while it is shown', async () => {
    const input = (await openSignIn()).getByLabelText('Password')
    expect(input.getAttribute('autocapitalize')).toBe('none')
    expect(input.getAttribute('autocorrect')).toBe('off')
    expect(input.getAttribute('spellcheck')).toBe('false')
    expect(input.getAttribute('autocomplete')).toBe('current-password')
  })
})
