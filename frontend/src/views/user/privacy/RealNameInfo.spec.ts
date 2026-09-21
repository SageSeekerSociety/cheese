// 这一页最长的一段是「使用场景与保护」那八张卡，翻完最容易漏掉一两张。
// 三件事：中文原文没变、英文整页无汉字、页脚那句里的 router-link 还插得进去
// （走 `#accessLog` 插槽，槽名对不上只会把 `{accessLog}` 印出来，不报错）。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RealNameInfo from './RealNameInfo.vue'

import i18n, { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'

vi.mock('@/network/api/users', () => ({ UserApi: { getRealNameInfo: vi.fn() } }))
vi.mock('@/services/account', async () => {
  const { ref } = await import('vue')
  return { default: { loggedIn: true }, currentUserId: ref('me') }
})

const CJK = /[㐀-䶿一-鿿豈-﫿]/

beforeEach(() => {
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(UserApi.getRealNameInfo).mockResolvedValue({
    data: {
      hasIdentity: true,
      identity: {
        realName: '张三',
        studentId: '2023001',
        grade: '2023',
        major: '计算机',
        className: '一班',
      },
    },
  } as unknown as Awaited<ReturnType<typeof UserApi.getRealNameInfo>>)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

async function mountPage(serverData = '2023001') {
  const stub = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: RealNameInfo },
      { path: '/access-logs', name: 'PrivacyCenterAccessLogs', component: stub },
      { path: '/settings/realname', name: 'UserSettingsRealName', component: stub },
    ],
  })
  await router.push('/')
  const view = render(RealNameInfo, {
    global: { plugins: [router, createPinia(), i18n, createVuetify({ components, directives })] },
  })
  // 卡片要等取回数据才画出来，等到服务端那份数据出现在屏幕上再断言。
  await view.findAllByText(serverData)
  return view
}

describe('privacy center real-name page', () => {
  it('还是说原来的那几句中文', async () => {
    setLocale('zh-CN')
    const view = await mountPage()

    expect(view.getByText('实名信息')).toBeTruthy()
    expect(view.getByText('已模糊处理')).toBeTruthy()
    expect(view.getByText('已完成认证')).toBeTruthy()
    expect(view.getByText('信息安全状态')).toBeTruthy()
    expect(view.getAllByText('访问记录').length).toBeGreaterThan(0)
    expect(view.getByText('实名信息的使用场景与保护')).toBeTruthy()
    expect(view.getByText('我们如何保护您的实名信息')).toBeTruthy()
  })

  it('renders every scenario and protection card from the catalog in English', async () => {
    setLocale('en')
    const view = await mountPage()

    expect(view.getByText('Masked')).toBeTruthy()
    expect(view.getByText('Verified')).toBeTruthy()
    expect(view.getByText('Identity check and mentor matching')).toBeTruthy()
    expect(view.getByText('Certifying a finished project')).toBeTruthy()
    expect(view.getByText('Awards and recognition')).toBeTruthy()
    expect(view.getByText('Course credit')).toBeTruthy()
    expect(view.getByText('Anonymous by default')).toBeTruthy()
    expect(view.getByText('Encrypted storage')).toBeTruthy()
    expect(view.getByText('Purpose limitation')).toBeTruthy()
    expect(view.getByText('Identity separation')).toBeTruthy()
    expect(view.getByText('Edit real-name details')).toBeTruthy()
  })

  it('keeps the access-log link inside the footer sentence', async () => {
    setLocale('en')
    const view = await mountPage()

    const link = view.container.querySelector('a.text-decoration-none')
    expect(link?.textContent?.trim()).toBe('the access log')
    expect(link?.getAttribute('href')).toBe('/access-logs')
    expect(view.container.textContent).not.toContain('{accessLog}')
  })

  it('leaves no Chinese but the details the server sent', async () => {
    setLocale('en')
    const view = await mountPage()

    const rendered = ['张三', '计算机', '一班'].reduce(
      (text, serverData) => text.replace(serverData, ''),
      view.container.textContent ?? ''
    )
    expect(rendered).not.toMatch(CJK)
  })

  it('says so when a field was never filled in', async () => {
    setLocale('en')
    vi.mocked(UserApi.getRealNameInfo).mockResolvedValue({
      data: { hasIdentity: false, identity: undefined },
    } as unknown as Awaited<ReturnType<typeof UserApi.getRealNameInfo>>)
    const view = await mountPage('Not filled in')

    expect(view.getAllByText('Not filled in').length).toBe(5)
    expect(view.getByText('Not verified')).toBeTruthy()
  })
})
