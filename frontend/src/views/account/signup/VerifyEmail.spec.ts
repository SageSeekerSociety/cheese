import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import VerifyEmail from './VerifyEmail.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { BusinessError } from '@/network/types/error'
import AccountService from '@/services/account'
import { useSignupStore } from '@/stores/signup'

vi.mock('@/network/api/users', () => ({
  UserApi: { register: vi.fn(), sendEmailCode: vi.fn() },
}))
vi.mock('@/network/api/legal', () => ({
  LegalApi: {
    listDocuments: vi.fn().mockResolvedValue({
      data: {
        documents: [
          { document: 'terms', version: '1.0' },
          { document: 'privacy', version: '1.0' },
        ],
      },
    }),
  },
}))
vi.mock('@/services/account', () => ({ default: { loggedIn: false, login: vi.fn() } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const blank = { template: '<div />' }

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})

async function open() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: blank },
      { path: '/account/signup', name: 'SignUpStart', component: blank },
      { path: '/account/signup/verify-email', component: VerifyEmail },
      { path: '/account/signin', name: 'SignIn', component: blank },
      { path: '/legal/terms', name: 'LegalTerms', component: blank },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: blank },
    ],
  })
  await router.push('/account/signup/verify-email')
  await router.isReady()
  const view = render(
    { template: '<router-view />' },
    { global: { plugins: [pinia, router, createVuetify({ components, directives })] } }
  )
  return { view, router }
}

async function typeCode(container: Element, code = '123456') {
  await fireEvent.paste(container.querySelector('.v-otp-input input')!, {
    clipboardData: { getData: () => code },
  })
}

/** Fill in the sign-up form's step one the way Start.vue does, in a fresh session. */
async function startSignup() {
  setActivePinia(createPinia())
  vi.mocked(UserApi.sendEmailCode).mockResolvedValue({} as never)
  await useSignupStore().startSignup({
    username: 'login-handle',
    nickname: 'DisplayName',
    email: 'user@example.com',
    password: 'Secret#123',
    consent: ticked,
  })
}

const ticked = { documents: { terms: '1.0', privacy: '1.0' }, method: 'checkbox' as const }

describe('verifying the email', () => {
  it('lands signed in once the account is created', async () => {
    await startSignup()
    const store = useSignupStore()
    const user = { id: 9, username: 'login-handle' }
    vi.mocked(UserApi.register).mockResolvedValue({ data: { user, accessToken: 'fresh-token' } } as never)
    const pinia = createPinia()
    setActivePinia(pinia)
    // Same tab, no refresh: the password is still in memory.
    useSignupStore().$patch({ ...store.$state })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: blank },
        { path: '/verify', component: VerifyEmail },
      ],
    })
    await router.push('/verify')
    await router.isReady()
    const { container } = render(
      { template: '<router-view />' },
      { global: { plugins: [pinia, router, createVuetify({ components, directives })] } }
    )

    await typeCode(container)

    await waitFor(() => expect(AccountService.login).toHaveBeenCalledWith('fresh-token', user))
    expect(UserApi.register).toHaveBeenCalledWith(
      expect.objectContaining({
        username: 'login-handle',
        emailCode: '123456',
        password: 'Secret#123',
        consent: ticked,
      })
    )
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/'))
  })

  it('survives a refresh without keeping the password, and asks for it again', async () => {
    await startSignup()
    expect(sessionStorage.getItem('cheese:signup')).not.toContain('Secret#123')
    vi.mocked(UserApi.register).mockResolvedValue({ data: { user: {}, accessToken: 't' } } as never)

    const { view } = await open() // a new pinia: what a reload would build
    expect(view.getByText('We sent a code to user@example.com')).toBeTruthy()

    await fireEvent.update(view.getByLabelText('Password'), 'Secret#123')
    await typeCode(view.container)

    await waitFor(() =>
      expect(UserApi.register).toHaveBeenCalledWith(
        expect.objectContaining({
          username: 'login-handle',
          email: 'user@example.com',
          password: 'Secret#123',
          consent: ticked,
        })
      )
    )
  })

  it('asks for consent again when it did not survive the refresh, and never assumes it', async () => {
    await startSignup()
    const saved = JSON.parse(sessionStorage.getItem('cheese:signup')!)
    sessionStorage.setItem('cheese:signup', JSON.stringify({ ...saved, consent: null }))
    vi.mocked(UserApi.register).mockResolvedValue({ data: { user: {}, accessToken: 't' } } as never)

    const { view } = await open()
    await fireEvent.update(view.getByLabelText('Password'), 'Secret#123')
    await typeCode(view.container)

    // Submitting without the tick opens the prompt; nothing is sent until it is answered.
    const agree = await view.findByRole('button', { name: 'Agree and sign up' })
    expect(UserApi.register).not.toHaveBeenCalled()
    const finish = view.getByRole('button', { name: 'Finish' })
    expect(finish.classList.contains('v-btn--loading')).toBe(false)
    await fireEvent.click(agree)

    await waitFor(() =>
      expect(UserApi.register).toHaveBeenCalledWith(
        expect.objectContaining({ consent: { documents: { terms: '1.0', privacy: '1.0' }, method: 'dialog' } })
      )
    )
  })

  it('states the wait after too many wrong codes, and holds the form back until it has passed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await startSignup()
    vi.mocked(UserApi.register).mockRejectedValue(
      new BusinessError('Too many failed attempts from this network', 403, {
        name: 'ForbiddenError',
        message: '',
        data: { reason: 'too_many_attempts', retryAfterSeconds: 90 },
      })
    )
    const { view } = await open()
    await fireEvent.update(view.getByLabelText('Password'), 'Secret#123')

    await typeCode(view.container)

    expect(await view.findByText('Too many attempts. Try again in 2 minutes.')).toBeTruthy()
    const finish = view.getByRole('button', { name: 'Finish' })
    expect(finish.hasAttribute('disabled')).toBe(true)

    vi.advanceTimersByTime(90_000)
    await waitFor(() => expect(finish.hasAttribute('disabled')).toBe(false))
  })

  it('says a wrong code is wrong in the interface language', async () => {
    await startSignup()
    vi.mocked(UserApi.register).mockRejectedValue(
      new BusinessError('Invalid or expired verification code', 422, {
        name: 'UnprocessableEntityError',
        message: 'Invalid or expired verification code',
        data: { reason: 'invalid_email_code' },
      })
    )
    setLocale('zh-CN')
    const { view } = await open()
    await fireEvent.update(view.getByLabelText('密码'), 'Secret#123')

    await typeCode(view.container)

    expect(await view.findByText('验证码不正确或已过期')).toBeTruthy()
    expect(view.queryByText(/Invalid or expired/)).toBeNull()
  })

  it('says how long to wait when a new code is asked for too soon', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await startSignup()
    const { view } = await open()
    vi.mocked(UserApi.sendEmailCode).mockRejectedValue(
      new BusinessError('Please wait before requesting a new code', 400, {
        name: 'BadRequestError',
        message: 'Please wait before requesting a new code',
        data: { reason: 'email_code_too_soon', retryAfterSeconds: 1800 },
      })
    )

    vi.advanceTimersByTime(61_000)
    await fireEvent.click(await view.findByRole('button', { name: 'Resend' }))

    expect(await view.findByText('Request a new code in 30 minutes.')).toBeTruthy()
  })

  it('sends the user back to the form when there is nothing to verify', async () => {
    const { router } = await open()
    await waitFor(() => expect(router.currentRoute.value.name).toBe('SignUpStart'))
  })

  it('resends the code through the send endpoint and counts down before the next one', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await startSignup()
    const { view } = await open()

    // The code was just sent, so resending waits out the minute.
    expect(view.queryByRole('button', { name: 'Resend' })).toBeNull()
    expect(view.getByText(/Resend in \d+s/)).toBeTruthy()

    vi.advanceTimersByTime(61_000)
    await fireEvent.click(await view.findByRole('button', { name: 'Resend' }))

    await waitFor(() => expect(UserApi.sendEmailCode).toHaveBeenLastCalledWith('user@example.com', undefined))
    expect(UserApi.sendEmailCode).toHaveBeenCalledTimes(2)
    expect(await view.findByText('Resend in 60s')).toBeTruthy()
  })
})
