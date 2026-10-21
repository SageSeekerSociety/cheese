// 好友的邀请链接（`/account/signup?code=…`）：打开就填好邀请码，注册时带上它——哪怕
// 注册本不要求邀请码；改走第三方登录注册，绕到服务商那边再回来，这个码也还在。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SignUp from './signup/Start.vue'
import OAuthComplete from './OAuthComplete.vue'

import { UserAuthApi } from '@/api/userAuth'
import { setLocale } from '@/i18n'

vi.mock('@/api/userAuth', () => ({
  UserAuthApi: {
    getRegistrationConfig: vi.fn(),
    sendEmailCode: vi.fn(),
    getOAuthState: vi.fn(),
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
vi.mock('@/services/account', () => ({ default: { loggedIn: false } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const blank = { template: '<div />' }

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  // Registration does not ask for a code: the link's code is still filled in and sent.
  vi.mocked(UserAuthApi.getRegistrationConfig).mockResolvedValue({ requireInviteCode: false } as never)
  vi.mocked(UserAuthApi.sendEmailCode).mockResolvedValue({} as never)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function openAt(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/signup', component: SignUp },
      { path: '/account/signup/verify-email', component: blank },
      { path: '/account/oauth/complete', name: 'OAuthComplete', component: OAuthComplete },
      { path: '/account/oauth/verify', name: 'OAuthVerify', component: blank },
      { path: '/account/signin', name: 'SignIn', component: blank },
      { path: '/legal/terms', name: 'LegalTerms', component: blank },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: blank },
    ],
  })
  await router.push(path)
  await router.isReady()
  const view = render(
    { template: '<router-view />' },
    { global: { plugins: [router, createPinia(), createVuetify({ components, directives })] } }
  )
  const submit = (await view.findByRole('button', { name: 'Create account' })) as HTMLButtonElement
  await waitFor(() => expect(submit.disabled).toBe(false)) // registration config loaded
  return view
}

const codeField = (view: ReturnType<typeof render>) => view.getByLabelText('Invitation code') as HTMLInputElement

describe('an invite link', () => {
  it('arrives at sign-up with the code filled in, and the sign-up carries it', async () => {
    const view = await openAt('/account/signup?code=FRIEND1')
    expect(codeField(view).value).toBe('FRIEND1')

    await fireEvent.update(view.getByLabelText('Username'), 'new-user')
    await fireEvent.update(view.getByLabelText('Display name'), 'NewUser')
    await fireEvent.update(view.getByLabelText('Email'), 'new@example.com')
    await fireEvent.update(view.getByLabelText('Password'), 'Secret#123')
    await fireEvent.update(view.getByLabelText('Confirm password'), 'Secret#123')
    await fireEvent.submit(view.container.querySelector('form')!)
    await fireEvent.click(await view.findByRole('button', { name: 'Agree and sign up' }))

    await waitFor(() => expect(UserAuthApi.sendEmailCode).toHaveBeenCalledWith('new@example.com', 'FRIEND1'))
  })

  it('keeps its code through a third-party sign-up that comes back without it', async () => {
    await openAt('/account/signup?code=FRIEND1')
    cleanup()

    vi.mocked(UserAuthApi.getOAuthState).mockResolvedValue({
      providerId: 'github',
      userInfo: { id: 'gh-1', name: 'Ada', email: 'ada@school.edu', verifiedEmail: 'ada@school.edu' },
      suggestedUsername: 'ada_lovelace',
      suggestedNickname: 'Ada',
    } as never)
    const view = await openAt('/account/oauth/complete?stateToken=token-1')
    expect(codeField(view).value).toBe('FRIEND1')

    await fireEvent.submit(view.container.querySelector('form')!)
    await fireEvent.click(await view.findByRole('button', { name: 'Agree and sign up' }))

    await waitFor(() =>
      expect(UserAuthApi.createUserFromOAuth).toHaveBeenCalledWith(expect.objectContaining({ inviteCode: 'FRIEND1' }))
    )
  })

  it('is not asked for when nobody sent one and registration does not need it', async () => {
    const view = await openAt('/account/signup')
    expect(view.queryByLabelText('Invitation code')).toBeNull()
  })
})
