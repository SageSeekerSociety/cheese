// 第三方登录新建账号：邮箱先用验证码证明，账号才建出来。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import OAuthComplete from './OAuthComplete.vue'

import { UserAuthApi } from '@/api/userAuth'
import { setLocale } from '@/i18n'

vi.mock('@/api/userAuth', () => ({
  UserAuthApi: {
    getOAuthState: vi.fn(),
    getRegistrationConfig: vi.fn(),
    sendOAuthEmailCode: vi.fn(),
    verifyOAuthEmail: vi.fn(),
    createUserFromOAuth: vi.fn(),
    bindOAuthToUser: vi.fn(),
  },
}))
vi.mock('@/api/legal', () => ({
  LegalApi: {
    listDocuments: vi.fn().mockResolvedValue({
      documents: [
        { document: 'terms', version: '1.0' },
        { document: 'privacy', version: '1.0' },
      ],
    }),
  },
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const blank = { template: '<div />' }

function state(userInfo: Record<string, string | null>) {
  return {
    providerId: 'github',
    userInfo: { id: 'gh-1', name: 'Ada', preferredUsername: 'ada', email: null, ...userInfo },
    suggestedUsername: 'ada_lovelace',
    suggestedNickname: 'Ada',
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(UserAuthApi.getRegistrationConfig).mockResolvedValue({ requireInviteCode: false } as never)
  vi.mocked(UserAuthApi.sendOAuthEmailCode).mockResolvedValue({} as never)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function open(userInfo: Record<string, string | null> = {}) {
  vi.mocked(UserAuthApi.getOAuthState).mockResolvedValue(state(userInfo) as never)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/oauth/complete', name: 'OAuthComplete', component: OAuthComplete },
      { path: '/account/oauth/verify', name: 'OAuthVerify', component: blank },
      { path: '/account/signin', name: 'SignIn', component: blank },
      { path: '/legal/terms', name: 'LegalTerms', component: blank },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: blank },
    ],
  })
  await router.push({ name: 'OAuthComplete', query: { stateToken: 'token-1' } })
  const view = render(
    { template: '<router-view />' },
    { global: { plugins: [router, createPinia(), createVuetify({ components, directives })] } }
  )
  const submit = (await view.findByRole('button', { name: 'Create account' })) as HTMLButtonElement
  await waitFor(() => expect(submit.disabled).toBe(false)) // registration config loaded
  return { view, router }
}

const submitForm = (view: ReturnType<typeof render>) => fireEvent.submit(view.container.querySelector('form')!)

async function createAccount(view: ReturnType<typeof render>) {
  await submitForm(view)
  await fireEvent.click(await view.findByRole('button', { name: 'Agree and sign up' }))
}

async function enterCode(view: ReturnType<typeof render>, code = '123456') {
  await view.findByText(/We sent a code to/)
  const inputs = view.container.querySelectorAll('.v-otp-input input')
  for (const [i, digit] of [...code].entries()) {
    await fireEvent.focus(inputs[i])
    await fireEvent.update(inputs[i], digit)
  }
}

const emailField = (view: ReturnType<typeof render>) => view.getByLabelText('Email') as HTMLInputElement

describe('the email of a new third-party account', () => {
  it('starts from the provider address and is proven with a code before the account is created', async () => {
    vi.mocked(UserAuthApi.verifyOAuthEmail).mockResolvedValue({ stateToken: 'token-2' } as never)
    const { view, router } = await open({ email: 'ada@school.edu' })
    expect(emailField(view).value).toBe('ada@school.edu')

    await createAccount(view)

    await waitFor(() =>
      expect(UserAuthApi.sendOAuthEmailCode).toHaveBeenCalledWith({ stateToken: 'token-1', email: 'ada@school.edu' })
    )
    await view.findByText('We sent a code to ada@school.edu')
    expect(UserAuthApi.createUserFromOAuth).not.toHaveBeenCalled()

    await enterCode(view)

    await waitFor(() =>
      expect(UserAuthApi.createUserFromOAuth).toHaveBeenCalledWith(
        expect.objectContaining({ stateToken: 'token-2', username: 'ada_lovelace', nickname: 'Ada' })
      )
    )
    expect(UserAuthApi.verifyOAuthEmail).toHaveBeenCalledWith({
      stateToken: 'token-1',
      email: 'ada@school.edu',
      code: '123456',
    })
    // A reload from here does not ask for the code again.
    expect(router.currentRoute.value.query.stateToken).toBe('token-2')
  })

  it('is asked for when the provider gave none', async () => {
    const { view } = await open()
    expect(emailField(view).value).toBe('')

    await submitForm(view)
    await view.findByText('Invalid email address')
    expect(UserAuthApi.sendOAuthEmailCode).not.toHaveBeenCalled()

    await fireEvent.update(emailField(view), 'ada@example.com')
    await createAccount(view)

    await waitFor(() =>
      expect(UserAuthApi.sendOAuthEmailCode).toHaveBeenCalledWith({ stateToken: 'token-1', email: 'ada@example.com' })
    )
  })

  it('stays on the code step after a wrong code, and creates nothing', async () => {
    vi.mocked(UserAuthApi.verifyOAuthEmail).mockRejectedValue({ error: { data: { reason: 'invalid_email_code' } } })
    const { view } = await open({ email: 'ada@school.edu' })
    await createAccount(view)

    await enterCode(view)

    await view.findByText('The code is wrong or has expired.')
    expect(UserAuthApi.createUserFromOAuth).not.toHaveBeenCalled()
  })

  it('can be changed from the code step', async () => {
    const { view } = await open({ email: 'ada@school.edu' })
    await createAccount(view)
    await view.findByText(/We sent a code to/)

    await fireEvent.click(view.getByRole('button', { name: 'Use another address' }))

    await view.findByLabelText('Email')
    expect(emailField(view).value).toBe('ada@school.edu')
  })

  it('belonging to an existing account leads to proving that account instead', async () => {
    const ownership = { type: 'password', email: 'ada_old', sessionId: 'pending-1' }
    vi.mocked(UserAuthApi.verifyOAuthEmail).mockResolvedValue({ ownership } as never)
    const { view, router } = await open({ email: 'ada@school.edu' })
    await createAccount(view)

    await enterCode(view)

    await waitFor(() => expect(router.currentRoute.value.name).toBe('OAuthVerify'))
    expect(router.currentRoute.value.query).toEqual(ownership)
    expect(UserAuthApi.createUserFromOAuth).not.toHaveBeenCalled()
  })

  it('once proven, is not asked for again after a reload', async () => {
    const { view } = await open({ email: 'ada@school.edu', verifiedEmail: 'ada@school.edu' })

    await createAccount(view)

    await waitFor(() =>
      expect(UserAuthApi.createUserFromOAuth).toHaveBeenCalledWith(expect.objectContaining({ stateToken: 'token-1' }))
    )
    expect(UserAuthApi.sendOAuthEmailCode).not.toHaveBeenCalled()
  })
})
