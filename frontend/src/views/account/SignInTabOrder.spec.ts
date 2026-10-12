// Tab from one box goes to the next box: a person who types the username and
// presses Tab expects to be typing the password, not sitting on 「忘记密码」.
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SignIn from './SignIn.vue'

import { setLocale } from '@/i18n'

vi.mock('@/api/userAuth', () => ({
  UserAuthApi: { getOAuthProviders: vi.fn().mockResolvedValue({ providers: [] }) },
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

/** Where Tab goes next from `from`, the way a browser walks elements with no positive tabindex. */
function nextTabStop(from: Element): Element | undefined {
  const stops = [...document.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')].filter(
    (el) => !(el as HTMLInputElement).disabled && el.getAttribute('tabindex') !== '-1' && !el.closest('[inert]')
  )
  return stops[stops.indexOf(from) + 1]
}

describe('sign-in tab order', () => {
  it('goes from the username to the password', async () => {
    const view = await openSignIn()
    expect(nextTabStop(view.getByLabelText('Username'))).toBe(view.getByLabelText('Password'))
  })

  it('still reaches the forgotten-password link, after the password', async () => {
    const view = await openSignIn()
    const link = view.getByRole('link', { name: /forgot/i })
    const stops = [...document.querySelectorAll('a[href], input')]
    expect(stops.indexOf(link)).toBeGreaterThan(stops.indexOf(view.getByLabelText('Password')))
  })
})
