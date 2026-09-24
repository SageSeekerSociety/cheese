/** Signing in with a code mailed to the account's address. */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { lastSignIn } from '../lastSignIn'

import Request from './Request.vue'
import Verify from './Verify.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { BusinessError } from '@/network/types/error'
import AccountService from '@/services/account'

vi.mock('@/network/api/users', () => ({
  UserApi: { requestSignInCode: vi.fn(), signInWithEmailCode: vi.fn() },
}))
vi.mock('@/services/account', () => ({ default: { loggedIn: false, login: vi.fn() } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const device = vi.hoisted(() => ({ canUnlockPasskeys: false }))
vi.mock('@simplewebauthn/browser', () => ({
  platformAuthenticatorIsAvailable: () => Promise.resolve(device.canUnlockPasskeys),
  startRegistration: vi.fn(),
}))

const blank = { template: '<div />' }
const user = { id: 7, username: 'existing-user' }

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  device.canUnlockPasskeys = false
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(UserApi.requestSignInCode).mockResolvedValue({} as never)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function open(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: blank },
      { path: '/projects/:id', component: blank },
      { path: '/account/signin', name: 'SignIn', component: blank },
      { path: '/account/signin/email', name: 'SignInEmailCode', component: Request },
      { path: '/account/signin/email/verify', name: 'SignInEmailCodeVerify', component: Verify },
      { path: '/account/verify-2fa', name: 'Verify2FA', component: blank },
      { path: '/account/passkey', name: 'PasskeyOffer', component: blank },
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

/** Ask for a code for this address from the entry screen, and arrive at the code screen. */
async function askForCode(address = 'someone@example.com', redirect?: string) {
  const opened = await open(`/account/signin/email${redirect ? `?redirect=${encodeURIComponent(redirect)}` : ''}`)
  await fireEvent.update(opened.view.getByLabelText('Email'), address)
  await fireEvent.submit(opened.view.container.querySelector('form')!)
  await waitFor(() => expect(opened.router.currentRoute.value.name).toBe('SignInEmailCodeVerify'))
  return opened
}

async function typeCode(container: Element, code = '123456') {
  await fireEvent.paste(container.querySelector('.v-otp-input input')!, {
    clipboardData: { getData: () => code },
  })
}

function refusal(status: number, data: Record<string, unknown>) {
  return new BusinessError('English text from the server', status, { name: 'Error', message: '', data })
}

describe('asking for a code', () => {
  it('sends it to the address given and names that address on the code screen', async () => {
    const { view } = await askForCode('someone@example.com')

    expect(UserApi.requestSignInCode).toHaveBeenCalledWith('someone@example.com')
    expect(await view.findByText('We sent a code to someone@example.com')).toBeTruthy()
  })

  it('says how long to wait when a code was asked for too recently', async () => {
    vi.mocked(UserApi.requestSignInCode).mockRejectedValue(
      refusal(400, { reason: 'email_code_too_soon', retryAfterSeconds: 42 })
    )
    const { view, router } = await open('/account/signin/email')
    await fireEvent.update(view.getByLabelText('Email'), 'someone@example.com')
    await fireEvent.submit(view.container.querySelector('form')!)

    expect(await view.findByText('Request a new code in 42 seconds.')).toBeTruthy()
    expect(router.currentRoute.value.name).toBe('SignInEmailCode')
  })

  it('sends someone who opens the code screen directly to enter an address first', async () => {
    const { router } = await open('/account/signin/email/verify')

    await waitFor(() => expect(router.currentRoute.value.name).toBe('SignInEmailCode'))
  })
})

describe('entering the code', () => {
  it('signs in, remembers the way for next time, and goes where the sign-in was headed', async () => {
    vi.mocked(UserApi.signInWithEmailCode).mockResolvedValue({
      data: { accessToken: 'fresh-token', user, requires2FA: false },
    } as never)
    const { view, router } = await askForCode('someone@example.com', '/projects/3')

    await typeCode(view.container, '654321')

    await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/projects/3'))
    expect(UserApi.signInWithEmailCode).toHaveBeenCalledWith({ email: 'someone@example.com', code: '654321' })
    expect(AccountService.login).toHaveBeenCalledWith('fresh-token', user)
    expect(lastSignIn()).toBe('email_code')
  })

  it('says a wrong or expired code is wrong, in the interface language', async () => {
    vi.mocked(UserApi.signInWithEmailCode).mockRejectedValue(refusal(401, { reason: 'invalid_email_code' }))
    const { view } = await askForCode()
    setLocale('zh-CN')

    await typeCode(view.container)

    expect(await view.findByText('验证码不正确或已过期')).toBeTruthy()
    expect(AccountService.login).not.toHaveBeenCalled()
  })

  it('leads an account with two-step verification to that step instead of signing in', async () => {
    vi.mocked(UserApi.signInWithEmailCode).mockResolvedValue({
      data: { requires2FA: true, tempToken: 'pending-ticket' },
    } as never)
    const { view, router } = await askForCode('someone@example.com', '/projects/3')

    await typeCode(view.container)

    await waitFor(() => expect(router.currentRoute.value.name).toBe('Verify2FA'))
    expect(router.currentRoute.value.query).toEqual({ token: 'pending-ticket', redirect: '/projects/3' })
    expect(AccountService.login).not.toHaveBeenCalled()
  })

  it('offers a passkey to an account due one, on its way to the home page', async () => {
    device.canUnlockPasskeys = true
    vi.mocked(UserApi.signInWithEmailCode).mockResolvedValue({
      data: {
        accessToken: 'fresh-token',
        user,
        requires2FA: false,
        passkeyEnrollment: { ticket: 'enrollment-ticket', offer: true, canStopAsking: false },
      },
    } as never)
    const { view, router } = await askForCode()

    await typeCode(view.container)

    await waitFor(() => expect(router.currentRoute.value.name).toBe('PasskeyOffer'))
  })

  it('asks for another code after a minute and counts down before the next one', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { view } = await askForCode('someone@example.com')
    expect(view.queryByRole('button', { name: 'Resend' })).toBeNull()

    vi.advanceTimersByTime(61_000)
    await fireEvent.click(await view.findByRole('button', { name: 'Resend' }))

    await waitFor(() => expect(UserApi.requestSignInCode).toHaveBeenCalledTimes(2))
    expect(UserApi.requestSignInCode).toHaveBeenLastCalledWith('someone@example.com')
    expect(await view.findByText('Resend in 60s')).toBeTruthy()
  })
})
