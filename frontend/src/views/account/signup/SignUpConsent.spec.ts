import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SignUp from './Start.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'

vi.mock('@/network/api/users', () => ({
  UserApi: {
    getRegistrationConfig: vi.fn().mockResolvedValue({ data: { requireInviteCode: false } }),
    sendEmailCode: vi.fn().mockResolvedValue({}),
  },
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
vi.mock('@/services/account', () => ({ default: { loggedIn: false } }))

const blank = { template: '<div />' }

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function openSignUp() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/signup', component: SignUp },
      { path: '/account/signup/verify-email', component: blank },
      { path: '/account/signin', component: blank },
      { path: '/legal/terms', name: 'LegalTerms', component: blank },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: blank },
    ],
  })
  await router.push('/account/signup')
  await router.isReady()
  const view = render(SignUp, {
    global: { plugins: [router, createPinia(), createVuetify({ components, directives })] },
  })
  await fireEvent.update(view.getByLabelText('Username'), 'new-user')
  await fireEvent.update(view.getByLabelText('Display name'), 'NewUser')
  await fireEvent.update(view.getByLabelText('Email'), 'new@example.com')
  await fireEvent.update(view.getByLabelText('Password'), 'Secret#123')
  await fireEvent.update(view.getByLabelText('Confirm password'), 'Secret#123')
  const submitButton = view.getByRole('button', { name: 'Create account' }) as HTMLButtonElement
  await waitFor(() => expect(submitButton.disabled).toBe(false)) // registration config loaded
  return { view, router, submitButton }
}

const isLoading = (button: HTMLElement) => button.classList.contains('v-btn--loading')

describe('signing up without ticking the agreement', () => {
  it('asks first, stays idle while asking, and sends only after 同意并注册', async () => {
    const { view, router, submitButton } = await openSignUp()

    await fireEvent.submit(view.container.querySelector('form')!)
    await view.findByRole('button', { name: 'Agree and sign up' })
    expect(isLoading(submitButton)).toBe(false)
    expect(UserApi.sendEmailCode).not.toHaveBeenCalled()

    // Cancelling leaves the form as it was: nothing sent, the button usable.
    await fireEvent.click(view.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(view.queryByRole('button', { name: 'Agree and sign up' })).toBeNull())
    expect(isLoading(submitButton)).toBe(false)
    expect(submitButton.disabled).toBe(false)
    expect(UserApi.sendEmailCode).not.toHaveBeenCalled()

    await fireEvent.submit(view.container.querySelector('form')!)
    await fireEvent.click(await view.findByRole('button', { name: 'Agree and sign up' }))

    await waitFor(() => expect(UserApi.sendEmailCode).toHaveBeenCalledWith('new@example.com', undefined))
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/account/signup/verify-email'))
  })
})
