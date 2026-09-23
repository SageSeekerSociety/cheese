import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SignUp from './signup/Start.vue'
import SignIn from './SignIn.vue'

import LanguageToggle from '@/components/common/LanguageToggle.vue'
import { resolveInitialLocale, setLocale } from '@/i18n'

vi.mock('@/network/api/users', () => ({
  UserApi: {
    getOAuthProviders: vi.fn().mockResolvedValue({ data: { providers: [] } }),
    getRegistrationConfig: vi.fn().mockResolvedValue({ data: { requireInviteCode: false } }),
  },
}))
vi.mock('@/services/account', () => ({ default: { loggedIn: false } }))
vi.mock('@/network/api/legal', () => ({
  LegalApi: { listDocuments: vi.fn().mockResolvedValue({ data: { documents: [] } }) },
}))

beforeEach(() => {
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function mount(page: typeof SignIn | typeof SignUp) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/legal/terms', name: 'LegalTerms', component: page },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: page },
      { path: '/:pathMatch(.*)*', component: page },
    ],
  })
  await router.push('/account/signin')
  return render(
    { components: { Page: page, LanguageToggle }, template: '<LanguageToggle /><Page />' },
    {
      global: { plugins: [router, createPinia(), createVuetify({ components, directives })] },
    }
  )
}

describe('account language', () => {
  it('switches sign-in labels while keeping typed credentials and the selected language', async () => {
    const view = await mount(SignIn)
    expect(view.getByRole('heading', { name: 'Sign in' })).toBeTruthy()
    await fireEvent.update(view.getByLabelText('Username'), 'existing-user')
    await fireEvent.click(view.getByRole('button', { name: '切换到中文' }))
    expect(view.getByRole('heading', { name: '登录' })).toBeTruthy()
    expect((view.getByLabelText('用户名') as HTMLInputElement).value).toBe('existing-user')
    expect(resolveInitialLocale()).toBe('zh-CN')
    await fireEvent.click(view.getByRole('button', { name: 'Switch to English' }))
    expect((view.getByLabelText('Username') as HTMLInputElement).value).toBe('existing-user')
    expect(view.getByText('Terms of Service')).toBeTruthy()
  })

  it('renders registration in English and updates custom validation after switching language', async () => {
    const view = await mount(SignUp)
    expect(view.getByRole('heading', { name: 'Create account' })).toBeTruthy()
    expect(view.getByLabelText('Display name')).toBeTruthy()
    await fireEvent.update(view.getByLabelText('Password', { exact: true }), 'password123')
    await fireEvent.blur(view.getByLabelText('Password', { exact: true }))
    expect(
      await view.findByText('Use at least 8 characters, with a letter, a number, and a special character')
    ).toBeTruthy()
    await fireEvent.click(view.getByRole('button', { name: '切换到中文' }))
    expect(await view.findByText('密码须至少 8 个字符，并包含字母、数字和特殊字符')).toBeTruthy()
    expect((view.getByLabelText('密码', { exact: true }) as HTMLInputElement).value).toBe('password123')
  })
})
