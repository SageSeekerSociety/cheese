/** Right after a password sign-in, the password manager that filled the
 *  password may keep a passkey too, without a dialog. When it will not, the
 *  person sees nothing at all. */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { toast } from 'vuetify-sonner'
import { startRegistration } from '@simplewebauthn/browser'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SignIn from './SignIn.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'

vi.mock('@/network/api/users', () => ({
  UserApi: {
    getOAuthProviders: vi.fn().mockResolvedValue({ data: { providers: [] } }),
    login: vi.fn(),
    getPasskeyRegistrationOptions: vi.fn(),
    verifyPasskeyRegistration: vi.fn(),
  },
}))
vi.mock('@/services/account', () => ({ default: { loggedIn: false, login: vi.fn() } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@simplewebauthn/browser', () => ({
  browserSupportsWebAuthn: () => false,
  browserSupportsWebAuthnAutofill: () => Promise.resolve(false),
  startAuthentication: vi.fn(),
  startRegistration: vi.fn(),
  WebAuthnAbortService: { cancelCeremony: vi.fn() },
}))

const user = { id: 7, username: 'existing-user' }
const options = { challenge: 'challenge', rp: { id: 'localhost' } }
const attestation = { id: 'new-passkey', response: {} }

function browserCanCreateQuietly(can: boolean) {
  vi.stubGlobal('PublicKeyCredential', {
    getClientCapabilities: () => Promise.resolve({ conditionalCreate: can }),
  })
}

async function signInWithPassword() {
  vi.mocked(UserApi.login).mockResolvedValue({
    data: {
      accessToken: 'token',
      user,
      passkeyEnrollment: { ticket: 'enrollment-ticket', offer: false, canStopAsking: false },
    },
  } as never)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/signin', component: SignIn },
      { path: '/', component: { template: '<div />' } },
      { path: '/legal/terms', name: 'LegalTerms', component: { template: '<div />' } },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: { template: '<div />' } },
    ],
  })
  await router.push('/account/signin')
  await router.isReady()
  const view = render(SignIn, {
    global: { plugins: [router, createPinia(), createVuetify({ components, directives })] },
  })
  await fireEvent.update(view.getByLabelText('Username'), 'existing-user')
  await fireEvent.update(view.getByLabelText('Password'), 'correct horse!1')
  await fireEvent.submit(view.container.querySelector('form')!)
  await waitFor(() => expect(router.currentRoute.value.path).toBe('/'))
  return view
}

beforeEach(() => {
  vi.clearAllMocks()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(UserApi.getPasskeyRegistrationOptions).mockResolvedValue({ data: { options } } as never)
  vi.mocked(UserApi.verifyPasskeyRegistration).mockResolvedValue({ data: {} } as never)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('a passkey created without asking, after a password sign-in', () => {
  it('is added to the account when the password manager creates it', async () => {
    browserCanCreateQuietly(true)
    vi.mocked(startRegistration).mockResolvedValue(attestation as never)

    await signInWithPassword()

    await waitFor(() => expect(UserApi.verifyPasskeyRegistration).toHaveBeenCalledWith(7, attestation))
    expect(UserApi.getPasskeyRegistrationOptions).toHaveBeenCalledWith(7, 'enrollment-ticket')
    expect(startRegistration).toHaveBeenCalledWith({ optionsJSON: options, useAutoRegister: true })
  })

  it('leaves no trace when the password manager declines', async () => {
    browserCanCreateQuietly(true)
    const declined = Object.assign(new Error('The request is not allowed'), { name: 'NotAllowedError' })
    vi.mocked(startRegistration).mockRejectedValue(declined)
    const error = vi.spyOn(console, 'error')
    const warn = vi.spyOn(console, 'warn')

    const view = await signInWithPassword()

    await waitFor(() => expect(startRegistration).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(UserApi.verifyPasskeyRegistration).not.toHaveBeenCalled()
    expect(view.container.querySelector('.v-alert')).toBeNull()
    expect(toast.error).not.toHaveBeenCalled()
    // The test router knows none of the page's other links; that is all it warns about.
    const noise = (calls: unknown[][]) => calls.filter(([m]) => !String(m).startsWith('[Vue Router warn]'))
    expect(noise(error.mock.calls)).toEqual([])
    expect(noise(warn.mock.calls)).toEqual([])
  })

  it('is never attempted where the browser would show a dialog instead', async () => {
    browserCanCreateQuietly(false)

    await signInWithPassword()

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(startRegistration).not.toHaveBeenCalled()
    expect(UserApi.getPasskeyRegistrationOptions).not.toHaveBeenCalled()
  })
})
