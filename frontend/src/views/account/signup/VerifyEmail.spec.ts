import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import VerifyEmail from './VerifyEmail.vue'

import { UserApi } from '@/network/api/users'
import { useSignupStore } from '@/stores/signup'

vi.mock('@/network/api/users', () => ({
  UserApi: { register: vi.fn() },
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})
beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('visualViewport', new EventTarget())
})

describe('registration completion', () => {
  it('carries the login username to sign-in after clearing registration state', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useSignupStore()
    store.$patch({
      username: 'login-handle',
      nickname: 'DisplayName',
      email: 'user@example.com',
      srpSalt: 'salt',
      srpVerifier: 'verifier',
    })
    vi.mocked(UserApi.register).mockResolvedValue({ data: { user: {} } } as never)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/verify', component: VerifyEmail },
        { path: '/account/signin', name: 'SignIn', component: { template: '<div />' } },
      ],
    })
    await router.push('/verify')
    await router.isReady()
    const { container } = render(VerifyEmail, {
      global: { plugins: [pinia, router, createVuetify({ components, directives })] },
    })

    await fireEvent.paste(container.querySelector('.v-otp-input input')!, {
      clipboardData: { getData: () => '123456' },
    })

    await waitFor(() => expect(router.currentRoute.value.name).toBe('SignIn'))
    expect(UserApi.register).toHaveBeenCalledWith(
      expect.objectContaining({ username: 'login-handle', nickname: 'DisplayName', emailCode: '123456' })
    )
    expect(store.username).toBe('')
    expect(store.srpVerifier).toBe('')
    expect(router.currentRoute.value.query.username).toBe('login-handle')
  })
})
