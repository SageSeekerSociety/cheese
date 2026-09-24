// 补邮箱这一屏：被路由拦下来的账号在这里验证一个邮箱，然后回到原本要去的地方。
import { reactive } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AddEmail from './AddEmail.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requireEmail } from '@/router/emailRequired'
import AccountService from '@/services/account'

vi.mock('@/network/api/users', () => ({
  UserApi: { sendAddEmailCode: vi.fn(), addEmail: vi.fn(), logout: vi.fn() },
}))
vi.mock('@/services/account', async () => {
  const { reactive } = await import('vue')
  return {
    default: reactive({
      loggedIn: true,
      user: null as { id: number; emailMissing?: boolean } | null,
      sessionRestored: Promise.resolve(),
      updateUserInfo: vi.fn(),
      logout: vi.fn(),
    }),
  }
})
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const blank = { template: '<div />' }

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  AccountService.loggedIn = true
  AccountService.user = reactive({ id: 1, emailMissing: true }) as never
  vi.mocked(UserApi.sendAddEmailCode).mockResolvedValue({} as never)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function signedInAt(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'Home', component: blank },
      { path: '/projects/:id', name: 'project', component: blank },
      { path: '/account/add-email', name: 'AccountAddEmail', component: AddEmail },
      { path: '/account/signin', name: 'SignIn', component: blank },
    ],
  })
  requireEmail(router, async () => AccountService)
  await router.push(path)
  const view = render(
    { template: '<router-view />' },
    { global: { plugins: [router, createPinia(), createVuetify({ components, directives })] } }
  )
  await view.findByText('Add an email address')
  return { view, router }
}

async function requestCode(view: ReturnType<typeof render>, email: string) {
  await fireEvent.update(view.getByLabelText('Email'), email)
  await fireEvent.submit(view.container.querySelector('form')!)
}

async function enterCode(view: ReturnType<typeof render>, code = '123456') {
  await view.findByText(/We sent a code to/)
  const inputs = view.container.querySelectorAll('.v-otp-input input')
  for (const [i, digit] of [...code].entries()) {
    await fireEvent.focus(inputs[i])
    await fireEvent.update(inputs[i], digit)
  }
}

describe('adding the email an account is missing', () => {
  it('stops the account on its way, verifies the address, then goes on to where it was going', async () => {
    vi.mocked(UserApi.addEmail).mockResolvedValue({ data: { user: { id: 1, emailMissing: false } } } as never)
    const { view, router } = await signedInAt('/projects/7')
    expect(router.currentRoute.value.name).toBe('AccountAddEmail')
    view.getByText('Your email is used to recover your account and to sign in')

    await requestCode(view, 'ada@example.com')
    await waitFor(() => expect(UserApi.sendAddEmailCode).toHaveBeenCalledWith('ada@example.com'))
    await enterCode(view)

    await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/projects/7'))
    expect(UserApi.addEmail).toHaveBeenCalledWith({ email: 'ada@example.com', code: '123456' })
  })

  it('keeps the account here after a wrong code', async () => {
    vi.mocked(UserApi.addEmail).mockRejectedValue({ error: { data: { reason: 'invalid_code' } } })
    const { view, router } = await signedInAt('/projects/7')
    await requestCode(view, 'ada@example.com')

    await enterCode(view)

    await view.findByText('The code is incorrect or has expired')
    expect(router.currentRoute.value.name).toBe('AccountAddEmail')
  })

  it('says so when the address belongs to another account', async () => {
    vi.mocked(UserApi.sendAddEmailCode).mockRejectedValue({ error: { data: { reason: 'email_taken' } } })
    const { view } = await signedInAt('/projects/7')

    await requestCode(view, 'taken@example.com')

    await view.findByText('This email is used by another account')
    expect(view.queryByText(/We sent a code to/)).toBeNull()
  })

  it('can sign out instead', async () => {
    const { view, router } = await signedInAt('/projects/7')

    await fireEvent.click(view.getByRole('button', { name: 'Sign out' }))

    await waitFor(() => expect(router.currentRoute.value.name).toBe('SignIn'))
    expect(UserApi.logout).toHaveBeenCalled()
    expect(AccountService.logout).toHaveBeenCalled()
  })
})
