/**
 * 「我的连接」上的飞书那一半：平台应用配好之后，成员这边是一颗按钮，不是一张表单。
 *
 * 这一份钉住三件事，每一件都是「坏了也不会有人立刻发现」的那种：
 *
 * 1. **平台没配应用时按钮是灰的**，而且点了什么也不会发生。按钮画亮、只在按下时报错，
 *    也「能用」—— 代价是每个人都要自己撞一次那句错，而这句话本来可以提前说。
 * 2. **点一下真的把人送到飞书**：先建自己那一行（`POST /me/integrations/feishu`，不带
 *    任何正文 —— 应用凭据是平台管理员的），再拿那一行的授权地址跳过去。少了前一步，
 *    回调没法知道是谁授权的；少了后一步，就永远停在原地。
 * 3. **请求还在飞的时候按钮是忙的**（`aria-busy`）：那一次跳转有两跳，中间那一拍里
 *    按钮看起来和没按过一样。
 *
 * 还有一条：**自带凭据的老连接照旧有它的「授权个人账号」按钮**，而走平台应用的那一行
 * 没有 —— 后者的连接方式就是上面那颗按钮（`shared_app` 说的就是这件事）。
 */
import type { Component } from 'vue'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listMyIntegrations = vi.fn()
const listMyMailDrafts = vi.fn()
const listProjects = vi.fn()
const feishuAvailability = vi.fn()
const connectFeishu = vi.fn()
const feishuAuthorizeUrl = vi.fn()

vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listMyIntegrations: () => listMyIntegrations(),
  listMyMailDrafts: () => listMyMailDrafts(),
  listProjects: () => listProjects(),
}))

// 飞书那几个函数住它们自己的模块（`../api/feishu`），所以按模块分别挡。
vi.mock('../api/feishu', async () => ({
  ...(await vi.importActual<typeof import('../api/feishu')>('../api/feishu')),
  feishuAvailability: () => feishuAvailability(),
  connectFeishu: () => connectFeishu(),
  feishuAuthorizeUrl: (id: string) => feishuAuthorizeUrl(id),
}))

// 授权回调是一次性动作，结果走全局 toast（§3.11）：这里只验它说了什么。
const sonner = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: { success: sonner.success, error: sonner.error } }))

import MyConnectionsView from './MyConnectionsView.vue'

import { setLocale } from '@/i18n'

/** 页面把浏览器送去哪了。`window.location.href = …` 在 happy-dom 里会真的去导航，
 *  所以这里换掉这一个属性 —— 路由用的是内存历史，不读它。 */
let navigatedTo = ''
function stubLocation() {
  Object.defineProperty(window.location, 'href', {
    configurable: true,
    get: () => navigatedTo,
    set: (value: string) => {
      navigatedTo = value
    },
  })
  navigatedTo = ''
}

const SHARED_ROW = {
  id: 'feishu-shared',
  provider: 'feishu',
  label: '飞书',
  owner_handle: 'user-1',
  config: {},
  grants: [],
  status: 'ok',
  last_error: '',
  last_checked_at: null,
  user_authorized: false,
  shared_app: true,
}
const OWN_APP_ROW = {
  ...SHARED_ROW,
  id: 'feishu-own',
  label: '飞书（自己建的）',
  config: { app_id: 'cli_own' },
  shared_app: false,
}

async function mount(url = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  await router.push(url)
  await router.isReady()
  return render(MyConnectionsView as unknown as Component, {
    global: {
      plugins: [createVuetify({ components, directives }), router],
      stubs: ['router-link'],
    },
  })
}

const connectButton = () => screen.getByRole('button', { name: '连接飞书' }) as HTMLButtonElement

beforeEach(() => {
  setLocale('zh-CN')
  stubLocation()
  listMyIntegrations.mockReset().mockResolvedValue({ data: [] })
  listMyMailDrafts.mockReset().mockResolvedValue({ data: [] })
  listProjects.mockReset().mockResolvedValue({ data: [] })
  feishuAvailability.mockReset().mockResolvedValue({ configured: true, app_id: 'cli_p', domain: 'feishu' })
  connectFeishu.mockReset()
  feishuAuthorizeUrl.mockReset()
  sonner.success.mockReset()
  sonner.error.mockReset()
})

afterEach(cleanup)

describe('「连接飞书」这颗按钮', () => {
  it('平台没配应用时是灰的，并说出原因', async () => {
    feishuAvailability.mockResolvedValue({ configured: false, app_id: '', domain: '' })
    await mount()

    await screen.findByText('管理员还没配置飞书应用')
    expect(connectButton().disabled).toBe(true)
  })

  it('问不到「配没配」时既不谎报没配，也不把整页连接一起掀掉', async () => {
    // 「没读到」和「没配」是两件事。前者的正确画法是照旧让人按，由服务端回那句实话 ——
    // 把没读到画成没配，是在替管理员说一句他可能没做过的事。
    listMyIntegrations.mockResolvedValue({ data: [SHARED_ROW] })
    feishuAvailability.mockRejectedValue(new Error('读不到'))
    await mount()

    expect(await screen.findByText('还没连接飞书')).toBeTruthy()
    expect(screen.queryByText('管理员还没配置飞书应用')).toBeNull()
    expect(connectButton().disabled).toBe(false)
  })

  it('配好了就走两步：先有自己的那一行，再去飞书的授权页', async () => {
    connectFeishu.mockResolvedValue(SHARED_ROW)
    feishuAuthorizeUrl.mockResolvedValue({ url: 'https://open.feishu.cn/authorize?x=1', redirect_uri: 'r' })
    await mount()

    await fireEvent.click(await screen.findByRole('button', { name: '连接飞书' }))

    await vi.waitFor(() => expect(navigatedTo).toBe('https://open.feishu.cn/authorize?x=1'))
    expect(connectFeishu).toHaveBeenCalledTimes(1)
    expect(feishuAuthorizeUrl).toHaveBeenCalledWith('feishu-shared')
  })

  it('两跳之间是忙的，不是又一颗看起来没按过的按钮', async () => {
    let release!: (row: unknown) => void
    connectFeishu.mockReturnValue(new Promise((resolve) => (release = resolve)))
    feishuAuthorizeUrl.mockResolvedValue({ url: 'https://open.feishu.cn/authorize?x=1', redirect_uri: 'r' })
    await mount()

    await fireEvent.click(await screen.findByRole('button', { name: '连接飞书' }))
    await vi.waitFor(() => expect(connectButton().getAttribute('aria-busy')).toBe('true'))
    expect(navigatedTo).toBe('')

    release(SHARED_ROW)
    await vi.waitFor(() => expect(navigatedTo).toBe('https://open.feishu.cn/authorize?x=1'))
  })
})

describe('已经有的连接', () => {
  it('走平台应用的那一行只等授权，自带凭据的那一行留着它自己的授权按钮', async () => {
    listMyIntegrations.mockResolvedValue({ data: [SHARED_ROW, OWN_APP_ROW] })
    await mount()

    // 走平台应用：连接的方式就是上面那颗按钮，所以这一行没有第二颗。
    expect(await screen.findByText('还没连接飞书')).toBeTruthy()
    expect(screen.getAllByText('授权个人账号（用于搜索）')).toHaveLength(1)
  })
})

describe('从飞书授权回来', () => {
  it("in English the outcome code and Feishu's own words are said in English", async () => {
    setLocale('en')
    await mount('/?feishu=denied&feishu_detail=access_denied')
    await vi.waitFor(() => expect(sonner.error).toHaveBeenCalledWith('Authorization was not granted: access_denied'))
  })

  it('a refusal of ours arrives as its sentence key', async () => {
    setLocale('en')
    await mount('/?feishu=feishuAppNotConfigured')
    await vi.waitFor(() =>
      expect(sonner.error).toHaveBeenCalledWith(
        "An admin hasn't set up the Feishu app yet, so Feishu can't be connected for now"
      )
    )
  })

  it('a code this build does not know says only that authorizing did not complete', async () => {
    setLocale('en')
    await mount('/?feishu=something_new')
    await vi.waitFor(() => expect(sonner.error).toHaveBeenCalledWith('Feishu authorization did not complete'))
  })

  it('成功了说成功', async () => {
    await mount('/?feishu=ok')
    await vi.waitFor(() => expect(sonner.success).toHaveBeenCalledWith('飞书授权成功'))
  })
})
