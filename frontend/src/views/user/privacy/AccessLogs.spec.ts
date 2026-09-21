// 访问记录页：表头、类型标签、页脚那段说明都进了词表。整页扫一遍汉字，另外单独
// 盯两处容易坏的地方：
//   * 右上角「N 条访问记录」是插值，漏了参数会印出 `{count}`；
//   * 页脚那句里嵌着一个 <a>，走的是 `#contact` 插槽，槽名对不上就会把
//     `{contact}` 原样印在屏幕上——不会报错。
import type { UserIdentityAccessLog } from '@/network/api/users/types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AccessLogs from './AccessLogs.vue'

import i18n, { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'

vi.mock('@/network/api/users', () => ({ UserApi: { getRealNameAccessLogs: vi.fn() } }))
vi.mock('@/services/account', async () => {
  const { ref } = await import('vue')
  return { default: { loggedIn: true }, currentUserId: ref('me') }
})

const CJK = /[㐀-䶿一-鿿豈-﫿]/

function accessLog(id: string, nickname: string, rest: Record<string, unknown>) {
  return {
    accessor: { id, nickname },
    accessTime: new Date(2026, 8, 17, 14, 30).getTime(),
    ipAddress: '127.0.0.1',
    ...rest,
  } as unknown as UserIdentityAccessLog
}

beforeEach(() => {
  vi.stubGlobal('visualViewport', new EventTarget())
  // 两条记录分别走「访问目的」的两条分支：一条带着赛题名，一条只有模块类型。
  const logs = [
    accessLog('someone', '张三', { accessType: 'VIEW', accessEntityName: '黑客松' }),
    accessLog('other', '李四', { accessType: 'EXPORT', accessModuleType: 'TASK' }),
  ]
  vi.mocked(UserApi.getRealNameAccessLogs).mockResolvedValue({
    data: { logs, page: { total: 2, nextStart: 2, hasMore: false } },
  } as unknown as Awaited<ReturnType<typeof UserApi.getRealNameAccessLogs>>)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

async function mountPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: AccessLogs }],
  })
  await router.push('/')
  const view = render(AccessLogs, {
    global: { plugins: [router, createPinia(), i18n, createVuetify({ components, directives })] },
  })
  // 记录是异步取回来的；昵称两种语言下都会渲染，等到它出现再断言。
  await view.findByText('张三')
  return view
}

describe('real-name access log page', () => {
  it('还是说原来的那几句中文', async () => {
    setLocale('zh-CN')
    const view = await mountPage()

    expect(view.getByText('实名信息访问记录')).toBeTruthy()
    expect(view.getByText('访问者')).toBeTruthy()
    expect(view.getByText('访问目的')).toBeTruthy()
    expect(view.getByText('2 条访问记录')).toBeTruthy()
    expect(view.getByText('赛题认证需要')).toBeTruthy()
    expect(view.getByText('已加载全部记录')).toBeTruthy()
  })

  it('renders headers, chips and the footer notice from the catalog in English', async () => {
    setLocale('en')
    const view = await mountPage()

    expect(view.getByText('Real-name access log')).toBeTruthy()
    expect(view.getByText('2 access records')).toBeTruthy()
    expect(view.getByText('Viewer')).toBeTruthy()
    expect(view.getByText('IP address')).toBeTruthy()
    expect(view.getByText('Viewed')).toBeTruthy()
    expect(view.getByText('Exported')).toBeTruthy()
    expect(view.getByText('Needed for contest verification')).toBeTruthy()
    expect(view.getByText('About access to your real-name info')).toBeTruthy()
    expect(view.getByText('That is every record')).toBeTruthy()
  })

  it('keeps the contact link inside the sentence instead of printing {contact}', async () => {
    setLocale('en')
    const view = await mountPage()

    const link = view.container.querySelector('a.text-decoration-none')
    expect(link?.textContent).toBe('contact us')
    expect(view.container.textContent).not.toContain('{contact}')
  })

  it('leaves no Chinese but the names the server sent', async () => {
    setLocale('en')
    const view = await mountPage()

    // 昵称和赛题名是后端数据，本来就是中文。
    const rendered = ['张三', '李四', '黑客松'].reduce(
      (text, serverData) => text.replace(serverData, ''),
      view.container.textContent ?? ''
    )
    expect(rendered).not.toMatch(CJK)
  })
})
