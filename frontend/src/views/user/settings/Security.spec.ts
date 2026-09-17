// 和 RealName.spec.ts 同一件事：这一页搬进词表之后，用英文真渲染一遍。
// 这一页还有一处「中文」不在词表里——`formatDate` 写死了 'zh-CN'，所以英文界面
// 仍会打印「2026年9月17日」。最后一条用例就是拦它的。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Security from './Security.vue'

import i18n, { setLocale } from '@/i18n'

// currentUserId 为 null 时这一页的每个 fetch 都在第一行早退，所以没有任何请求。
vi.mock('@/network/api/users', () => ({
  UserApi: {
    getUserPasskeys: vi.fn(),
    get2FAStatus: vi.fn(),
    getPasskeyRegistrationOptions: vi.fn(),
    verifyPasskeyRegistration: vi.fn(),
    deletePasskey: vi.fn(),
  },
}))
vi.mock('@/services/account', async () => {
  const { ref } = await import('vue')
  return { default: { loggedIn: true }, currentUserId: ref(null), currentUserName: ref(null) }
})

const CJK = /[㐀-䶿一-鿿豈-﫿]/

beforeEach(() => {
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function mountPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: Security }],
  })
  await router.push('/user/settings/security')
  return render(Security, {
    global: { plugins: [router, createPinia(), i18n, createVuetify({ components, directives })] },
  })
}

describe('security settings in English', () => {
  it('renders the three cards from the catalog', async () => {
    const view = await mountPage()

    expect(view.getByText('Account security')).toBeTruthy()
    expect(view.getByText('Login password')).toBeTruthy()
    expect(view.getByText('Change password')).toBeTruthy()
    expect(view.getByText('Passkeys')).toBeTruthy()
    expect(view.getByText('No passkeys yet')).toBeTruthy()
    expect(view.getByText('Two-factor authentication')).toBeTruthy()
    expect(view.getByText('Not active')).toBeTruthy()
    expect(view.getByText('Turn on two-factor')).toBeTruthy()
  })

  it('renders the change-password dialog from the catalog', async () => {
    const view = await mountPage()

    await fireEvent.click(view.getByText('Change password'))

    expect(await view.findByText('Change login password')).toBeTruthy()
    expect(view.getByLabelText('New password')).toBeTruthy()
    expect(view.getByLabelText('Confirm new password')).toBeTruthy()
    expect(view.getByText('Change it')).toBeTruthy()
  })

  it('leaves no Chinese anywhere on the rendered page, dialog included', async () => {
    const view = await mountPage()
    await fireEvent.click(view.getByText('Change password'))
    await view.findByText('Change login password')

    expect(view.baseElement.textContent ?? '').not.toMatch(CJK)
  })
})
