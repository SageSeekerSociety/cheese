/**
 * 「我的连接」上的飞书那一半：一颗「连接飞书」，打开一张填成员自己公司应用凭据的表单。
 *
 * 这一份钉住几件事，每一件都是「坏了也不会有人立刻发现」的那种：
 *
 * 1. **填完真的把人送到飞书**：先建自己那一行（`POST /me/integrations/feishu`，带上 App
 *    ID / Secret），再拿那一行的授权地址跳过去。少了前一步，回调没法知道是谁授权的；少了
 *    后一步，就永远停在原地。
 * 2. **凭据不对时服务端那句话留在表单上**，不跳走、不关表单。
 * 3. **已经连上的飞书那一行有自己的「授权个人账号」**：没授权完或过期了从那里再走一次。
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
const feishuSetup = vi.fn()
const connectFeishu = vi.fn()
const feishuAuthorizeUrl = vi.fn()
const claimFeishuHandoff = vi.fn()

vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listMyIntegrations: () => listMyIntegrations(),
  listMyMailDrafts: () => listMyMailDrafts(),
  listProjects: () => listProjects(),
}))

// 飞书那几个函数住它们自己的模块（`../api/feishu`），所以按模块分别挡。
vi.mock('../api/feishu', async () => ({
  ...(await vi.importActual<typeof import('../api/feishu')>('../api/feishu')),
  feishuSetup: () => feishuSetup(),
  connectFeishu: (app: unknown) => connectFeishu(app),
  feishuAuthorizeUrl: (id: string) => feishuAuthorizeUrl(id),
  claimFeishuHandoff: (integration: string, handoff: string) => claimFeishuHandoff(integration, handoff),
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

const FEISHU_ROW = {
  id: 'feishu-own',
  provider: 'feishu',
  label: '飞书',
  owner_handle: 'user-1',
  config: { app_id: 'cli_own', domain: 'feishu' },
  grants: [],
  status: 'ok',
  last_error: '',
  last_checked_at: null,
  user_authorized: false,
}
const REDIRECT = 'https://okcheese.com/api/integrations/feishu/callback'

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
      stubs: {
        'router-link': true,
        // 对话框原地摊开：测试环境里浮层挂不上，开着的时候里面的东西照常画。
        AdaptiveDialog: {
          props: ['modelValue', 'title', 'primaryLabel'],
          emits: ['primary'],
          template:
            '<div v-if="modelValue"><h2>{{ title }}</h2><slot /><button @click="$emit(\'primary\')">{{ primaryLabel }}</button></div>',
        },
      },
    },
  })
}

/** 点「连接飞书」，在表单上填好一套凭据，按「保存并授权」。 */
async function fillAndSave(secret: string) {
  await fireEvent.click(await screen.findByRole('button', { name: '连接飞书' }))
  await fireEvent.update(await screen.findByLabelText('App ID'), 'cli_other')
  await fireEvent.update(screen.getByLabelText('App Secret'), secret)
  await fireEvent.click(screen.getByRole('button', { name: '保存并授权' }))
}

beforeEach(() => {
  setLocale('zh-CN')
  stubLocation()
  listMyIntegrations.mockReset().mockResolvedValue({ data: [] })
  listMyMailDrafts.mockReset().mockResolvedValue({ data: [] })
  listProjects.mockReset().mockResolvedValue({ data: [] })
  feishuSetup.mockReset().mockResolvedValue({ redirect_uri: REDIRECT })
  connectFeishu.mockReset()
  feishuAuthorizeUrl.mockReset()
  sonner.success.mockReset()
  sonner.error.mockReset()
})

afterEach(cleanup)

describe('「连接飞书」', () => {
  it('打开表单，表单上写着要填的回调地址；填完先建自己那一行，再去飞书的授权页', async () => {
    connectFeishu.mockResolvedValue(FEISHU_ROW)
    feishuAuthorizeUrl.mockResolvedValue({ url: 'https://open.feishu.cn/authorize?own=1', redirect_uri: 'r' })
    await mount()

    await fireEvent.click(await screen.findByRole('button', { name: '连接飞书' }))
    expect(await screen.findByDisplayValue(REDIRECT)).toBeTruthy()
    await fireEvent.update(screen.getByLabelText('App ID'), 'cli_other')
    await fireEvent.update(screen.getByLabelText('App Secret'), 'other-secret')
    await fireEvent.click(screen.getByRole('button', { name: '保存并授权' }))

    await vi.waitFor(() => expect(navigatedTo).toBe('https://open.feishu.cn/authorize?own=1'))
    expect(connectFeishu).toHaveBeenCalledWith({ domain: 'feishu', app_id: 'cli_other', app_secret: 'other-secret' })
    expect(feishuAuthorizeUrl).toHaveBeenCalledWith('feishu-own')
  })

  it('凭据不对时，服务端那句话留在表单上，不跳走', async () => {
    connectFeishu.mockRejectedValue(new Error('以应用身份取令牌：飞书返回 10014 app secret invalid'))
    await mount()

    await fillAndSave('typo')

    expect(await screen.findByText('以应用身份取令牌：飞书返回 10014 app secret invalid')).toBeTruthy()
    expect(navigatedTo).toBe('')
    expect(feishuAuthorizeUrl).not.toHaveBeenCalled()
  })

  it('问不到回调地址也照样能连，不把整页连接一起掀掉', async () => {
    listMyIntegrations.mockResolvedValue({ data: [FEISHU_ROW] })
    feishuSetup.mockRejectedValue(new Error('读不到'))
    connectFeishu.mockResolvedValue(FEISHU_ROW)
    feishuAuthorizeUrl.mockResolvedValue({ url: 'https://open.feishu.cn/authorize?x=1', redirect_uri: 'r' })
    await mount()

    expect(await screen.findByText('还没连接飞书')).toBeTruthy()
    await fillAndSave('other-secret')
    await vi.waitFor(() => expect(navigatedTo).toBe('https://open.feishu.cn/authorize?x=1'))
  })
})

describe('已经有的连接', () => {
  it('飞书那一行没授权完时说还没连上，并留着它自己的授权按钮', async () => {
    listMyIntegrations.mockResolvedValue({ data: [FEISHU_ROW] })
    await mount()

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
    await mount('/?feishu=feishuDomainInvalid')
    await vi.waitFor(() => expect(sonner.error).toHaveBeenCalledWith('The domain must be feishu or lark'))
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

  // 回调不存东西，只留一个领取号：这一页登录着去领，领回来的才是这次授权的结果。只有
  // 发起授权的人领得到，所以别人点了授权链接，令牌也进不了这一行。
  it('回来时先领取这次授权，领到的结果说一次', async () => {
    claimFeishuHandoff.mockResolvedValueOnce({ authorization: { feishu: 'ok' } })
    await mount('/?feishu_integration=i1&feishu_handoff=h1')
    await vi.waitFor(() => expect(sonner.success).toHaveBeenCalledWith('飞书授权成功'))
    expect(claimFeishuHandoff).toHaveBeenCalledWith('i1', 'h1')
  })

  it('领不到（别人的授权、过期了）就说链接无效，不当成功', async () => {
    setLocale('en')
    claimFeishuHandoff.mockResolvedValueOnce({ authorization: { feishu: 'invalid_link' } })
    await mount('/?feishu_integration=i1&feishu_handoff=h2')
    await vi.waitFor(() => expect(sonner.error).toHaveBeenCalled())
    expect(sonner.success).not.toHaveBeenCalled()
  })
})
