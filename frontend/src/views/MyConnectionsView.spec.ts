/**
 * 「我的连接」上的飞书那一半：一颗「连接飞书」，打开一张选单 —— 本人所在团队设的飞书应用，
 * 选一个就跳去飞书授权；一个都没设时，指到能设的地方。
 *
 * 这一份钉住几件事，每一件都是「坏了也不会有人立刻发现」的那种：
 *
 * 1. **选一个团队的应用真的把人送到飞书**：先建自己那一行（`POST /me/integrations/feishu`，
 *    带团队 id），再拿那一行的授权地址跳过去。
 * 2. **一个都没设时不是一张空白**：说清楚，并指到个人设置里个人团队的那一栏。
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
const feishuChoices = vi.fn()
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
  feishuChoices: () => feishuChoices(),
  connectFeishu: (teamId: unknown) => connectFeishu(teamId),
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
  config: { team_id: 12, folders: [] },
  grants: [],
  status: 'ok',
  last_error: '',
  last_checked_at: null,
  user_authorized: false,
}
const PERSONAL = { team_id: 11, team_name: '个人', team_handle: '', personal: true, can_manage: true, app: null }
const TEAM = {
  team_id: 12,
  team_name: '课程小队',
  team_handle: 'course-team',
  personal: false,
  can_manage: false,
  app: { app_id: 'cli_team', domain: 'feishu' },
}

async function mount(url = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      // Where the empty state points to set a Feishu app up.
      { path: '/settings/feishu', name: 'UserSettingsFeishu', component: { template: '<div />' } },
      { path: '/teams/:handle/feishu', name: 'TeamsDetailFeishu', component: { template: '<div />' } },
    ],
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
        ConfirmDialog: {
          props: ['modelValue', 'title', 'confirmLabel'],
          emits: ['confirm'],
          template:
            '<div v-if="modelValue" role="dialog"><h2>{{ title }}</h2><slot /><button @click="$emit(\'confirm\')">{{ confirmLabel }}</button></div>',
        },
      },
    },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  stubLocation()
  listMyIntegrations.mockReset().mockResolvedValue({ data: [] })
  listMyMailDrafts.mockReset().mockResolvedValue({ data: [] })
  listProjects.mockReset().mockResolvedValue({ data: [] })
  feishuChoices.mockReset().mockResolvedValue({ teams: [PERSONAL, TEAM], redirect_uri: 'r' })
  connectFeishu.mockReset()
  feishuAuthorizeUrl.mockReset()
  sonner.success.mockReset()
  sonner.error.mockReset()
})

afterEach(cleanup)

describe('「连接飞书」', () => {
  it('列出团队设的应用，选一个：先建自己那一行，再去飞书的授权页', async () => {
    connectFeishu.mockResolvedValue(FEISHU_ROW)
    feishuAuthorizeUrl.mockResolvedValue({ url: 'https://open.feishu.cn/authorize?team=1', redirect_uri: 'r' })
    await mount()

    await fireEvent.click(await screen.findByRole('button', { name: '连接飞书' }))
    expect(await screen.findByText('cli_team · feishu')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: '用这个连接' }))

    await vi.waitFor(() => expect(navigatedTo).toBe('https://open.feishu.cn/authorize?team=1'))
    expect(connectFeishu).toHaveBeenCalledWith(12)
    expect(feishuAuthorizeUrl).toHaveBeenCalledWith('feishu-own')
  })

  it('一个都没设时说清楚，并指到个人设置里去设', async () => {
    feishuChoices.mockResolvedValue({ teams: [PERSONAL], redirect_uri: 'r' })
    await mount()

    await fireEvent.click(await screen.findByRole('button', { name: '连接飞书' }))
    expect(await screen.findByText('你所在的团队都还没设置飞书应用。')).toBeTruthy()
    expect(await screen.findByText('在个人设置里设置')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '用这个连接' })).toBeNull()
  })

  it('问不到团队的应用也不把整页连接一起掀掉', async () => {
    listMyIntegrations.mockResolvedValue({ data: [FEISHU_ROW] })
    feishuChoices.mockRejectedValue(new Error('读不到'))
    await mount()

    expect(await screen.findByText('还没连接飞书')).toBeTruthy()
  })

  it('问不到回调地址时表单上说出来，并能再问一次', async () => {
    feishuSetup.mockRejectedValueOnce(new Error('网络断了'))
    await mount()

    await fireEvent.click(await screen.findByRole('button', { name: '连接飞书' }))
    expect(await screen.findByText('没能取到重定向 URL（网络断了）')).toBeTruthy()
    expect(screen.queryByDisplayValue(REDIRECT)).toBeNull()

    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByDisplayValue(REDIRECT)).toBeTruthy()
    expect(screen.queryByText('没能取到重定向 URL（网络断了）')).toBeNull()
  })
})

describe('已经有的连接', () => {
  it('飞书那一行没授权完时说还没连上，并留着它自己的授权按钮', async () => {
    listMyIntegrations.mockResolvedValue({ data: [FEISHU_ROW] })
    await mount()

    expect(await screen.findByText('还没连接飞书')).toBeTruthy()
    expect(screen.getAllByText('授权个人账号（用于搜索）')).toHaveLength(1)
  })

  it('两家公司的飞书应用各成一行，列表和删除确认都说得出是哪一个', async () => {
    const other = { ...FEISHU_ROW, id: 'feishu-other', label: 'Lark', config: { app_id: 'cli_other', domain: 'lark' } }
    listMyIntegrations.mockResolvedValue({ data: [FEISHU_ROW, other] })
    await mount()

    expect(await screen.findByText('飞书 · cli_own')).toBeTruthy()
    expect(screen.getByText('Lark · cli_other')).toBeTruthy()

    await fireEvent.click(screen.getAllByRole('button', { name: '删除' })[1]!)
    expect(await screen.findByText('删除「Lark · cli_other」')).toBeTruthy()
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
