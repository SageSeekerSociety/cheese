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
