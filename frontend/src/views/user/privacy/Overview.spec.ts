// 这一页原来整页都是硬编码中文。源码闸门数的是源码里的字，这份用例数的是渲染
// 出来的字，两边合起来才算这一页真的翻了。三件事各自会坏：
//   * 漏掉一句——整页扫一遍，英文下不该剩下任何汉字；
//   * 访问记录那句话的主语是 `#who` 具名插槽，写错不报错，只会把 `{who}` 原样
//     印在屏幕上；
//   * 日期按语序走：这一页原来给 `toLocaleString` 写死了 'zh-CN'，英文界面下
//     会显示成 2026/09/17。
import type { UserIdentityAccessLog } from '@/network/api/users/types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Overview from './Overview.vue'

import i18n, { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'

vi.mock('@/network/api/users', () => ({ UserApi: { getRealNameAccessLogs: vi.fn() } }))
// 记录里的两个 accessor：一个就是本人，一个不是，两条分支都要走到。
vi.mock('@/services/account', async () => {
  const { ref } = await import('vue')
  return { default: { loggedIn: true }, currentUserId: ref('me') }
})

const CJK = /[㐀-䶿一-鿿豈-﫿]/
const ACCESS_TIME = new Date(2026, 8, 17, 14, 30).getTime()

// 页面只读 accessor 的 id / nickname / avatarId 三个字段，别的按类型补不了也不该补。
function accessLog(id: string, nickname: string, reason: Record<string, unknown>) {
  return {
    accessor: { id, nickname },
    accessTime: ACCESS_TIME,
    accessType: 'VIEW',
    ipAddress: '127.0.0.1',
    ...reason,
  } as unknown as UserIdentityAccessLog
}

function mockLogs(logs: UserIdentityAccessLog[]) {
  vi.mocked(UserApi.getRealNameAccessLogs).mockResolvedValue({
    data: { logs, page: { total: logs.length } },
  } as unknown as Awaited<ReturnType<typeof UserApi.getRealNameAccessLogs>>)
}

beforeEach(() => {
  vi.stubGlobal('visualViewport', new EventTarget())
  mockLogs([
    accessLog('me', '李四', { accessEntityName: '黑客松' }),
    accessLog('you', '张三', { accessModuleType: 'TASK' }),
  ])
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

async function mountPage() {
  // 页面里三张快捷卡片是带 name 的 router-link；名字对不上会渲染成一条死链。
  const stub = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Overview },
      { path: '/access-logs', name: 'PrivacyCenterAccessLogs', component: stub },
      { path: '/real-name', name: 'PrivacyCenterRealNameInfo', component: stub },
      { path: '/data-sharing', name: 'PrivacyCenterDataSharing', component: stub },
    ],
  })
  await router.push('/')
  const view = render(Overview, {
    global: { plugins: [router, createPinia(), i18n, createVuetify({ components, directives })] },
  })
  // 记录是异步取回来的，等到它渲染出来再断言。
  // 记录是异步取回来的；昵称两种语言下都会渲染，等到它出现再断言。
  await view.findByText('张三')
  return view
}

describe('privacy overview page', () => {
  it('还是说原来的那几句中文', async () => {
    setLocale('zh-CN')
    const view = await mountPage()

    expect(view.getByText('隐私概览')).toBeTruthy()
    expect(view.getByText('近期活动')).toBeTruthy()
    expect(view.getByText('快捷操作')).toBeTruthy()
    expect(view.container.textContent).toContain('看了自己的实名信息')
    expect(view.container.textContent).toContain('看了您的实名信息')
    expect(view.container.textContent).toContain('用于赛题认证')
  })

  it('renders the access log from the catalog in English', async () => {
    setLocale('en')
    const view = await mountPage()

    expect(view.getByText('Privacy overview')).toBeTruthy()
    expect(view.getByText('Recent activity')).toBeTruthy()
    expect(view.getByText('Quick actions')).toBeTruthy()
    expect(view.getByText('Used for contest verification')).toBeTruthy()
    expect(view.getByText('Used for 黑客松')).toBeTruthy()
  })

  it('keeps the subject in its own slot instead of printing {who}', async () => {
    setLocale('en')
    const view = await mountPage()

    const titles = Array.from(view.container.querySelectorAll('.v-list-item-title')).map((el) => el.textContent?.trim())
    expect(titles).toEqual(['You viewed your own real-name info', '张三 viewed your real-name info'])
    // 主语仍然是那个加粗的 span，没有被拆成两段普通文本。
    const subjects = Array.from(view.container.querySelectorAll('.v-list-item-title .font-weight-medium')).map(
      (el) => el.textContent
    )
    expect(subjects).toEqual(['You', '张三'])
    expect(view.container.textContent).not.toContain('{who}')
  })

  it('writes the date in the language on screen', async () => {
    setLocale('en')
    const view = await mountPage()
    expect(view.container.textContent).toContain('09/17/2026, 02:30 PM')

    cleanup()
    setLocale('zh-CN')
    const zh = await mountPage()
    expect(zh.container.textContent).toContain('2026/09/17 14:30')
  })

  it('leaves no Chinese on the page in English except the names the server sent', async () => {
    setLocale('en')
    const view = await mountPage()

    // 昵称和赛题名是后端数据，本来就是中文；页面自己写的那几句不该有。
    const rendered = (view.container.textContent ?? '').replace('张三', '').replace('黑客松', '')
    expect(rendered).not.toMatch(CJK)
  })
})
