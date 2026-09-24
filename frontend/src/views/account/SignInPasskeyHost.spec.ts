/** On an address the passkey's site does not cover, the browser refuses before
 *  asking anything; the page says where passkeys do work. */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SignIn from './SignIn.vue'

import { setLocale } from '@/i18n'

vi.mock('@/network/api/users', () => ({
  UserApi: {
    getOAuthProviders: vi.fn().mockResolvedValue({ data: { providers: [] } }),
    getPasskeyAuthenticationOptions: vi.fn().mockResolvedValue({
      data: { options: { challenge: 'c', rpId: 'okcheese.com' } },
    }),
  },
}))
vi.mock('@/services/account', () => ({ default: { loggedIn: false, login: vi.fn() } }))
vi.mock('@simplewebauthn/browser', () => ({
  browserSupportsWebAuthn: () => true,
  browserSupportsWebAuthnAutofill: () => Promise.resolve(false),
  startAuthentication: vi.fn().mockRejectedValue(
    Object.assign(new Error('The RP ID "okcheese.com" is invalid for this domain'), {
      name: 'SecurityError',
      code: 'ERROR_INVALID_RP_ID',
    })
  ),
  WebAuthnAbortService: { cancelCeremony: vi.fn() },
}))

beforeEach(() => {
  localStorage.clear()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('a passkey on an address its site does not cover', () => {
  it('names the address where passkeys work', async () => {
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
    const view = render(SignIn, {
      global: { plugins: [router, createPinia(), createVuetify({ components, directives })] },
    })

    await fireEvent.click(await view.findByRole('button', { name: /Sign in with a passkey/ }))

    expect(await view.findByText('Passkeys can’t be used at this address. Go to okcheese.com instead.')).toBeTruthy()
  })
})
