import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SudoVerify from './SudoVerify.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { useSudoStore } from '@/stores/sudo'

// Only what re-authenticating with a password may touch: anything else it
// called would be undefined here and fail the verification.
vi.mock('@/network/api/users', () => ({
  UserApi: {
    getAuthMethods: vi.fn(),
    verifySudoPassword: vi.fn(),
  },
}))
vi.mock('@/services/account', () => ({
  currentUserId: ref(7),
  currentUserName: ref('existing-user'),
}))
vi.mock('@simplewebauthn/browser', () => ({
  browserSupportsWebAuthn: () => false,
  startAuthentication: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('re-authenticating with a password', () => {
  it('sends the password itself', async () => {
    vi.mocked(UserApi.getAuthMethods).mockResolvedValue({
      data: { supports_passkey: false, supports_2fa: false, requires_2fa: false },
    } as never)
    vi.mocked(UserApi.verifySudoPassword).mockResolvedValue({
      data: { verified: true, sudoTicket: 'ticket' },
    } as never)
    const pinia = createPinia()
    setActivePinia(pinia)
    const sudo = useSudoStore()
    sudo.setRetryOperation({ opKey: 'changePassword', returnPath: '/settings/security' })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/account/sudo', component: SudoVerify },
        { path: '/settings/security', component: { template: '<div />' } },
      ],
    })
    await router.push('/account/sudo')
    await router.isReady()
    const view = render(SudoVerify, {
      global: { plugins: [pinia, router, createVuetify({ components, directives })] },
    })

    await fireEvent.update(await view.findByLabelText('Password'), 'correct horse!1')
    await fireEvent.submit(view.container.querySelector('form')!)

    await waitFor(() => expect(router.currentRoute.value.path).toBe('/settings/security'))
    expect(UserApi.verifySudoPassword).toHaveBeenCalledWith('correct horse!1', 'password:change')
    expect(sudo.consumeTicket()).toBe('ticket')
  })
})

describe('the interruption explains itself and can be backed out of', () => {
  async function mountFor(opKey: string) {
    vi.mocked(UserApi.getAuthMethods).mockResolvedValue({
      data: { supports_passkey: false, supports_2fa: false, requires_2fa: false },
    } as never)
    const pinia = createPinia()
    setActivePinia(pinia)
    const sudo = useSudoStore()
    sudo.setRetryOperation({ opKey, returnPath: '/settings/security' })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/account/sudo', component: SudoVerify },
        { path: '/settings/security', component: { template: '<div />' } },
      ],
    })
    await router.push('/account/sudo')
    await router.isReady()
    const view = render(SudoVerify, {
      global: { plugins: [pinia, router, createVuetify({ components, directives })] },
    })
    return { view, router, sudo }
  }

  it('names the change being confirmed', async () => {
    const { view } = await mountFor('changePassword')
    expect(await view.findByText('Confirm it’s you before you change your password')).toBeTruthy()
  })

  it('goes back without the pending change when cancelled', async () => {
    const { view, router, sudo } = await mountFor('changePassword')

    await fireEvent.click(await view.findByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(router.currentRoute.value.path).toBe('/settings/security'))
    expect(sudo.needsRetry).toBe(false)
    expect(UserApi.verifySudoPassword).not.toHaveBeenCalled()
  })
})
