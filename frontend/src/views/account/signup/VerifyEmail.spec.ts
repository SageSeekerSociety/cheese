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
import AccountService from '@/services/account'
import { useSignupStore } from '@/stores/signup'

vi.mock('@/network/api/users', () => ({
  UserApi: { register: vi.fn(), sendEmailCode: vi.fn() },
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
  })
}

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
      expect.objectContaining({ username: 'login-handle', emailCode: '123456', password: 'Secret#123' })
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
        expect.objectContaining({ username: 'login-handle', email: 'user@example.com', password: 'Secret#123' })
      )
    )
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
