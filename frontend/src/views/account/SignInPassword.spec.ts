import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SignIn from './SignIn.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { BusinessError } from '@/network/types/error'
import AccountService from '@/services/account'

// Only what signing in with a password may touch: anything else it called
// would be undefined here and fail the sign-in.
vi.mock('@/network/api/users', () => ({
  UserApi: {
    getOAuthProviders: vi.fn().mockResolvedValue({ data: { providers: [] } }),
    login: vi.fn(),
  },
}))
vi.mock('@/services/account', () => ({ default: { loggedIn: false, login: vi.fn() } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => {
  vi.clearAllMocks()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('password sign-in', () => {
  it('sends the username and password to the login endpoint and signs in', async () => {
    const user = { id: 7, username: 'existing-user' }
    vi.mocked(UserApi.login).mockResolvedValue({ data: { accessToken: 'token', user } } as never)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/account/signin', component: SignIn },
        { path: '/', component: { template: '<div />' } },
        // The page links to both legal documents by name (#1486).
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

    await waitFor(() => expect(AccountService.login).toHaveBeenCalledWith('token', user))
    expect(UserApi.login).toHaveBeenCalledWith(
      expect.objectContaining({ username: 'existing-user', password: 'correct horse!1' })
    )
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/'))
  })
})

describe('a wrong password', () => {
  async function openSignIn() {
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
    return view
  }

  async function signIn(view: ReturnType<typeof render>, labels = { username: 'Username', password: 'Password' }) {
    await fireEvent.update(view.getByLabelText(labels.username), 'existing-user')
    await fireEvent.update(view.getByLabelText(labels.password), 'not it!1')
    await fireEvent.submit(view.container.querySelector('form')!)
  }

  const wrong = (data: Record<string, unknown>) =>
    new BusinessError('Invalid username or password', 401, { name: 'AuthenticationRequiredError', message: '', data })

  afterEach(() => vi.useRealTimers())

  it('says so in the interface language', async () => {
    vi.mocked(UserApi.login).mockRejectedValue(wrong({ reason: 'invalid_credentials' }))
    const view = await openSignIn()

    await signIn(view)

    expect(await view.findByText('Wrong username or password.')).toBeTruthy()
    expect(view.getByRole('button', { name: 'Sign in' }).hasAttribute('disabled')).toBe(false)
  })

  it('states the wait it starts, and holds the form back until it has passed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    setLocale('zh-CN')
    vi.mocked(UserApi.login).mockRejectedValue(wrong({ reason: 'invalid_credentials', retryAfterSeconds: 30 }))
    const view = await openSignIn()

    await signIn(view, { username: '用户名', password: '密码' })

    expect(await view.findByText('用户名或密码错误，请在 30 秒后重试')).toBeTruthy()
    const submit = view.getByRole('button', { name: '登录' })
    expect(submit.hasAttribute('disabled')).toBe(true)
    await fireEvent.submit(view.container.querySelector('form')!)
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(UserApi.login).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(30_000)
    await waitFor(() => expect(submit.hasAttribute('disabled')).toBe(false))
  })

  it('states the wait when too many attempts have been refused', async () => {
    vi.mocked(UserApi.login).mockRejectedValue(
      new BusinessError('Too many failed attempts from this network', 403, {
        name: 'ForbiddenError',
        message: '',
        data: { reason: 'too_many_attempts', retryAfterSeconds: 240 },
      })
    )
    const view = await openSignIn()

    await signIn(view)

    expect(await view.findByText('Too many attempts. Try again in 4 minutes.')).toBeTruthy()
  })
})
