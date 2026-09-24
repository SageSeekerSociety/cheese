/** After signing in with a password, someone whose account has no passkey may
 *  be offered one, on one screen, once in a while — and only when nothing
 *  better is waiting for them. */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { platformAuthenticatorIsAvailable, startAuthentication, startRegistration } from '@simplewebauthn/browser'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { pendingSudo } from '@/utils/sudo'

import { endPasskeyOffer } from './passkeyEnrollment'
import PasskeyOffer from './PasskeyOffer.vue'
import SignIn from './SignIn.vue'
import Verify2FA from './Verify2FA.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'

vi.mock('@/network/api/users', () => ({
  UserApi: {
    getOAuthProviders: vi.fn().mockResolvedValue({ data: { providers: [] } }),
    login: vi.fn(),
    verify2FA: vi.fn(),
    getPasskeyAuthenticationOptions: vi.fn(),
    verifyPasskeyAuthentication: vi.fn(),
    getPasskeyRegistrationOptions: vi.fn(),
    verifyPasskeyRegistration: vi.fn(),
    dismissPasskeyPrompt: vi.fn(),
  },
}))
vi.mock('@/services/account', async () => {
  const { ref } = await import('vue')
  return {
    default: { loggedIn: false, login: vi.fn() },
    currentUserId: ref(7),
    currentUserName: ref('existing-user'),
  }
})
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@simplewebauthn/browser', () => ({
  browserSupportsWebAuthn: () => true,
  browserSupportsWebAuthnAutofill: () => Promise.resolve(false),
  platformAuthenticatorIsAvailable: vi.fn(),
  startAuthentication: vi.fn(),
  startRegistration: vi.fn(),
  WebAuthnAbortService: { cancelCeremony: vi.fn() },
}))

const user = { id: 7, username: 'existing-user' }
const options = { challenge: 'challenge', rp: { id: 'localhost' } }
const attestation = { id: 'new-passkey', response: {} }
const blank = { template: '<div />' }
const TICKET = 'enrollment-ticket'
const CONFIRMED = 'ticket-from-confirming'

function enrollment(overrides: Partial<UserApi.PasskeyEnrollment> = {}): UserApi.PasskeyEnrollment {
  return { ticket: TICKET, offer: true, canStopAsking: false, ...overrides }
}

function passwordManager(answer: 'creates' | 'declines' | 'cannot') {
  vi.stubGlobal('PublicKeyCredential', {
    getClientCapabilities: () => Promise.resolve({ conditionalCreate: answer !== 'cannot' }),
  })
  vi.mocked(startRegistration).mockImplementation(async ({ useAutoRegister }) => {
    if (useAutoRegister && answer !== 'creates') {
      throw Object.assign(new Error('not allowed'), { name: 'NotAllowedError' })
    }
    return attestation as never
  })
}

async function open(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: blank },
      { path: '/account/signin', name: 'SignIn', component: SignIn },
      { path: '/account/verify-2fa', name: 'Verify2FA', component: Verify2FA },
      { path: '/account/passkey', name: 'PasskeyOffer', component: PasskeyOffer },
      { path: '/projects/:id', component: blank },
      { path: '/legal/terms', name: 'LegalTerms', component: blank },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: blank },
      { path: '/:rest(.*)*', component: blank },
    ],
  })
  await router.push(path)
  await router.isReady()
  const view = render(
    { template: '<router-view />' },
    { global: { plugins: [router, createPinia(), createVuetify({ components, directives })] } }
  )
  return { view, router }
}

async function signInWithPassword(passkeyEnrollment: UserApi.PasskeyEnrollment | undefined, path = '/account/signin') {
  vi.mocked(UserApi.login).mockResolvedValue({ data: { accessToken: 'token', user, passkeyEnrollment } } as never)
  return submitPassword(path)
}

async function submitPassword(path = '/account/signin') {
  const opened = await open(path)
  await fireEvent.update(opened.view.getByLabelText('Username'), 'existing-user')
  await fireEvent.update(opened.view.getByLabelText('Password'), 'correct horse!1')
  await fireEvent.submit(opened.view.container.querySelector('form')!)
  return opened
}

// Filling the sixth digit submits, as it does for a person typing.
async function enterCode(view: ReturnType<typeof render>) {
  await waitFor(() => expect(view.container.querySelectorAll('input').length).toBeGreaterThanOrEqual(6))
  const inputs = view.container.querySelectorAll('input')
  for (const [i, digit] of [...'123456'].entries()) {
    await fireEvent.focus(inputs[i])
    await fireEvent.update(inputs[i], digit)
  }
}

async function landsOn(router: ReturnType<typeof createRouter>, path: string) {
  await waitFor(() => expect(router.currentRoute.value.path).toBe(path), { timeout: 3000 })
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(platformAuthenticatorIsAvailable).mockResolvedValue(true)
  vi.mocked(UserApi.getPasskeyRegistrationOptions).mockImplementation(async (_userId, ticket) => {
    if (ticket !== TICKET && ticket !== CONFIRMED) throw new Error('no such ticket')
    return { data: { options } } as never
  })
  vi.mocked(UserApi.verifyPasskeyRegistration).mockResolvedValue({ data: {} } as never)
  vi.mocked(UserApi.dismissPasskeyPrompt).mockResolvedValue({ data: {} } as never)
  passwordManager('declines')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  // What one test's sign-in left in memory is not another test's sign-in.
  endPasskeyOffer()
})

describe('who is offered a passkey after signing in', () => {
  it('someone who signed in with a password and has none', async () => {
    const { view, router } = await signInWithPassword(enrollment())

    await landsOn(router, '/account/passkey')
    expect(await view.findByText('Sign in without a password next time')).toBeTruthy()
  })

  it('not someone whose password manager just added one', async () => {
    passwordManager('creates')

    const { router } = await signInWithPassword(enrollment())

    await landsOn(router, '/')
    await waitFor(() => expect(UserApi.verifyPasskeyRegistration).toHaveBeenCalledWith(7, attestation))
  })

  it('not an account the server says is not due it', async () => {
    const { router } = await signInWithPassword(enrollment({ offer: false }))

    await landsOn(router, '/')
  })

  it('not on a device that cannot unlock a passkey with a fingerprint, face or screen lock', async () => {
    vi.mocked(platformAuthenticatorIsAvailable).mockResolvedValue(false)

    const { router } = await signInWithPassword(enrollment())

    await landsOn(router, '/')
  })

  it('not someone on the way to a particular page', async () => {
    const { router } = await signInWithPassword(enrollment(), '/account/signin?redirect=/projects/3')

    await landsOn(router, '/projects/3')
  })

  it('not someone who signed in with a passkey', async () => {
    vi.mocked(UserApi.getPasskeyAuthenticationOptions).mockResolvedValue({ data: { options: {} } } as never)
    vi.mocked(startAuthentication).mockResolvedValue({ id: 'assertion' } as never)
    vi.mocked(UserApi.verifyPasskeyAuthentication).mockResolvedValue({
      data: { accessToken: 'token', user, passkeyEnrollment: enrollment() },
    } as never)
    const { view, router } = await open('/account/signin')

    await fireEvent.click(view.getByText('Sign in with a passkey'))

    await landsOn(router, '/')
  })

  it('someone who finished a second step after the password', async () => {
    vi.mocked(UserApi.login).mockResolvedValue({ data: { requires2FA: true, tempToken: 'temp' } } as never)
    vi.mocked(UserApi.verify2FA).mockResolvedValue({
      data: { accessToken: 'token', user, usedBackupCode: false, passkeyEnrollment: enrollment() },
    } as never)
    const { view, router } = await submitPassword()
    await landsOn(router, '/account/verify-2fa')

    await enterCode(view)

    await landsOn(router, '/account/passkey')
  })

  it('not someone who left a password sign-in halfway and came back through another provider', async () => {
    vi.mocked(UserApi.login).mockResolvedValue({ data: { requires2FA: true, tempToken: 'temp' } } as never)
    vi.mocked(UserApi.verify2FA).mockResolvedValue({
      data: { accessToken: 'token', user, usedBackupCode: false, passkeyEnrollment: enrollment() },
    } as never)
    const abandoned = await submitPassword()
    await landsOn(abandoned.router, '/account/verify-2fa')
    cleanup()
    // Back on the sign-in page, this time leaving for another provider.
    await open('/account/signin')
    cleanup()

    const { view, router } = await open('/account/verify-2fa?token=temp')
    await enterCode(view)

    await landsOn(router, '/')
  })

  it('not someone who reached the second step from another provider', async () => {
    vi.mocked(UserApi.verify2FA).mockResolvedValue({
      data: { accessToken: 'token', user, usedBackupCode: false, passkeyEnrollment: enrollment() },
    } as never)
    const { view, router } = await open('/account/verify-2fa?token=temp')

    await enterCode(view)

    await landsOn(router, '/')
  })
})

describe('the offer', () => {
  async function offered(overrides: Partial<UserApi.PasskeyEnrollment> = {}) {
    const opened = await signInWithPassword(enrollment(overrides))
    await landsOn(opened.router, '/account/passkey')
    await opened.view.findByText('Add a passkey')
    return opened
  }

  it('adds a passkey with the sign-in’s own ticket, then goes home', async () => {
    const { view, router } = await offered()

    await fireEvent.click(view.getByText('Add a passkey'))

    await landsOn(router, '/')
    expect(startRegistration).toHaveBeenLastCalledWith({ optionsJSON: options, useAutoRegister: false })
    expect(UserApi.verifyPasskeyRegistration).toHaveBeenCalledWith(7, attestation)
    // The quiet attempt and the button share the one ticket.
    expect(UserApi.getPasskeyRegistrationOptions).toHaveBeenCalledTimes(1)
  })

  it('stays, and says so, when the browser dialog is dismissed', async () => {
    const { view, router } = await offered()
    vi.mocked(startRegistration).mockRejectedValue(Object.assign(new Error('x'), { name: 'NotAllowedError' }))

    await fireEvent.click(view.getByText('Add a passkey'))

    expect(await view.findByText('Adding the passkey was canceled')).toBeTruthy()
    expect(router.currentRoute.value.path).toBe('/account/passkey')
  })

  // The identity dialog lives in the app shell; `pendingSudo` is what it shows
  // and answers, so the tests answer it the way the dialog would.
  async function confirmationAsked() {
    await waitFor(() => expect(pendingSudo.value?.purpose).toBe('passkey:add'))
    return pendingSudo.value!
  }

  it('asks the person to confirm it is them, in place, once the sign-in is a few minutes old', async () => {
    const { view, router } = await offered()
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(Date.now() + 5 * 60 * 1000)

    await fireEvent.click(view.getByText('Add a passkey'))
    expect(router.currentRoute.value.path).toBe('/account/passkey')
    ;(await confirmationAsked()).settle(CONFIRMED)

    await landsOn(router, '/')
    expect(UserApi.getPasskeyRegistrationOptions).toHaveBeenLastCalledWith(7, CONFIRMED)
    expect(UserApi.verifyPasskeyRegistration).toHaveBeenCalledWith(7, attestation)
  })

  it('stays quietly when that confirmation is closed', async () => {
    const { view, router } = await offered()
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(Date.now() + 5 * 60 * 1000)

    await fireEvent.click(view.getByText('Add a passkey'))
    ;(await confirmationAsked()).settle(null)

    await waitFor(() => expect(view.getByText('Add a passkey').closest('button')?.disabled).toBe(false))
    expect(router.currentRoute.value.path).toBe('/account/passkey')
    expect(view.container.querySelector('.v-alert')).toBeNull()
  })

  it('“Not now” holds it back and goes home', async () => {
    const { view, router } = await offered()

    await fireEvent.click(view.getByText('Not now'))

    await landsOn(router, '/')
    expect(UserApi.dismissPasskeyPrompt).toHaveBeenCalledWith(7, false)
  })

  it('offers “Don’t ask again” only once it has been declined before', async () => {
    const first = await offered()
    expect(first.view.queryByText('Don’t ask again')).toBeNull()
    cleanup()

    const { view, router } = await offered({ canStopAsking: true })
    await fireEvent.click(view.getByText('Don’t ask again'))

    await landsOn(router, '/')
    expect(UserApi.dismissPasskeyPrompt).toHaveBeenCalledWith(7, true)
  })

  it('is not there to open without a sign-in', async () => {
    const { router } = await open('/account/passkey')

    await landsOn(router, '/')
  })
})
