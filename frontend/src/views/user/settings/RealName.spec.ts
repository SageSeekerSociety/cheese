// 这一页搬进词表之后，用英文真的渲染一遍。两件事要一起证明：
//   * 整页渲染出来没有剩下一句硬编码中文——`i18n-cjk-ratchet` 数的是源码里的
//     字符串，这里数的是渲染结果，两边合起来才算这一页真的翻了；
//   * 隐私横幅里那两处 `<strong>` 还插得进去。它是全站唯一一处用 `i18n-t`
//     具名插槽的文案，写错了不会报错，只会把 `{encrypted}` 原样印在屏幕上。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RealName from './RealName.vue'

import i18n, { setLocale } from '@/i18n'

// 页面挂载时读一次 currentUserId；给 null 就走「没有身份信息」的那条早退分支，
// 一次网络请求都不发。这里要的是文案，不是数据流。
vi.mock('@/network/api/users', () => ({
  UserApi: { getRealNameInfo: vi.fn(), patchRealNameInfo: vi.fn() },
}))
vi.mock('@/services/account', async () => {
  const { ref } = await import('vue')
  return { default: { loggedIn: true }, currentUserId: ref(null) }
})

const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\u8c48-\ufaff]/

beforeEach(() => {
  setLocale('en')
  // VOverlay 定位时要读它，happy-dom 没有。
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function mountPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: RealName }],
  })
  await router.push('/user/settings/realname')
  return render(RealName, {
    global: { plugins: [router, createPinia(), i18n, createVuetify({ components, directives })] },
  })
}

/** 横幅整条可点，点开就是那个隐私说明对话框。 */
async function openPrivacyDialog(view: Awaited<ReturnType<typeof mountPage>>) {
  const banner = view.container.querySelector('.privacy-banner')
  expect(banner).toBeTruthy()
  await fireEvent.click(banner!)
  await view.findByText('Got it')
}

describe('real-name settings in English', () => {
  it('renders labels, hints and the privacy dialog from the catalog', async () => {
    const view = await mountPage()

    expect(view.getAllByText('Real name').length).toBeGreaterThan(0)
    expect(view.getByPlaceholderText('Enter your real name')).toBeTruthy()
    expect(view.getByLabelText('Student ID')).toBeTruthy()
    expect(view.getByLabelText('Year')).toBeTruthy()
    expect(view.getByText('Academic information')).toBeTruthy()
    expect(view.getByText('Identity verification')).toBeTruthy()
    expect(view.getByText('Save information')).toBeTruthy()

    await openPrivacyDialog(view)
    expect(view.getByText('How we protect your real name')).toBeTruthy()
    expect(view.getByText('Encrypted storage')).toBeTruthy()
    expect(view.getByText('Got it')).toBeTruthy()
  })

  it('puts the <strong> back into the banner instead of printing the placeholder', async () => {
    const view = await mountPage()

    const banner = view.container.querySelector('.privacy-banner')
    expect(banner).toBeTruthy()
    const strong = Array.from(banner!.querySelectorAll('strong')).map((el) => el.textContent)
    expect(strong).toEqual(['securely encrypted', 'fully anonymous'])
    expect(banner!.textContent).not.toContain('{encrypted}')
    expect(banner!.textContent).not.toContain('{anonymous}')
  })

  it('leaves no Chinese anywhere on the rendered page, dialog included', async () => {
    const view = await mountPage()
    await openPrivacyDialog(view)

    // 对话框是 teleport 到 body 的，所以查整份文档而不是 container。
    expect(view.baseElement.textContent ?? '').not.toMatch(CJK)
  })
})
