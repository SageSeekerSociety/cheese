/**
 * 「我的连接」上的飞书那一半：一格「连接飞书」，列出本人所在的团队（个人团队在前），各说
 * 团队的应用和本人连没连，并指到那个团队的「飞书应用」—— 连接在那里做。
 *
 * 钉住：列表的顺序、状态和去处；一个都没设时说清楚并指到个人设置；读不到也不掀掉整页；
 * 已经连上的飞书那一行留着自己的「授权个人账号」。
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
const feishuAuthorizeUrl = vi.fn()
const claimFeishuHandoff = vi.fn()
const deleteIntegration = vi.fn()
const checkIntegration = vi.fn()

vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listMyIntegrations: () => listMyIntegrations(),
  listMyMailDrafts: () => listMyMailDrafts(),
  listProjects: () => listProjects(),
  deleteIntegration: (id: string) => deleteIntegration(id),
  checkIntegration: (id: string) => checkIntegration(id),
}))

// 飞书那几个函数住它们自己的模块（`../api/feishu`），所以按模块分别挡。
vi.mock('../api/feishu', async () => ({
  ...(await vi.importActual<typeof import('../api/feishu')>('../api/feishu')),
  feishuChoices: () => feishuChoices(),
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
const PERSONAL = {
  team_id: 11,
  team_name: '个人',
  team_handle: '',
  personal: true,
  can_manage: true,
  app: null,
  connection: null,
}
const TEAM = {
  team_id: 12,
  team_name: '课程小队',
  team_handle: 'course-team',
  personal: false,
  can_manage: false,
  app: { app_id: 'cli_team', domain: 'feishu' },
  connection: null,
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
  feishuAuthorizeUrl.mockReset()
  deleteIntegration.mockReset()
  checkIntegration.mockReset()
  sonner.success.mockReset()
  sonner.error.mockReset()
})

afterEach(cleanup)

describe('「连接飞书」那一格', () => {
  it('列出本人所在的团队，个人团队在前，各说应用和本人连没连，并指到那个团队的「飞书应用」', async () => {
    feishuChoices.mockResolvedValue({
      teams: [PERSONAL, { ...TEAM, connection: { id: 'feishu-own', status: 'ok', user_authorized: true } }],
      redirect_uri: 'r',
    })
    await mount()

    const rows = await screen.findAllByText(/^(个人|课程小队)$/)
    expect(rows.map((r) => r.textContent?.trim())).toEqual(['个人', '课程小队'])
    expect(screen.getByText('cli_team · 已连接')).toBeTruthy()
    expect(screen.getByText('还没设置飞书应用')).toBeTruthy()
    // 连接在团队那一栏里做：这里只有去那里的链接，没有一张选单。
    expect((screen.getByText('打开') as HTMLAnchorElement).getAttribute('href')).toBe('/teams/course-team/feishu')
    expect((screen.getByText('去设置') as HTMLAnchorElement).getAttribute('href')).toBe('/settings/feishu')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('一个都没设时说清楚，个人那一行指到个人设置', async () => {
    feishuChoices.mockResolvedValue({ teams: [PERSONAL], redirect_uri: 'r' })
    await mount()

    expect(((await screen.findByText('去设置')) as HTMLAnchorElement).getAttribute('href')).toBe('/settings/feishu')
    expect(screen.getByText(/你所在的团队都还没设置飞书应用/)).toBeTruthy()
  })

  it('问不到团队的应用也不把整页连接一起掀掉', async () => {
    listMyIntegrations.mockResolvedValue({ data: [FEISHU_ROW] })
    feishuChoices.mockRejectedValue(new Error('读不到'))
    await mount()

    expect(await screen.findByText('还没连接飞书')).toBeTruthy()
  })
})

describe('「连接飞书」那一格跟着连接走', () => {
  it('在这里删掉飞书连接，团队那一格不再说已连接', async () => {
    const connected = { ...TEAM, connection: { id: 'feishu-own', status: 'ok', user_authorized: true } }
    listMyIntegrations.mockResolvedValue({ data: [FEISHU_ROW] })
    feishuChoices
      .mockResolvedValueOnce({ teams: [PERSONAL, connected], redirect_uri: 'r' })
      .mockResolvedValue({ teams: [PERSONAL, TEAM], redirect_uri: 'r' })
    deleteIntegration.mockResolvedValue({ deleted: 'feishu-own' })
    await mount()

    expect(await screen.findByText('cli_team · 已连接')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: '删除' }))
    await fireEvent.click(screen.getAllByRole('button', { name: '删除' }).at(-1)!)
    expect(await screen.findByText('cli_team · 还没连接')).toBeTruthy()
    expect(screen.queryByText('cli_team · 已连接')).toBeNull()
  })

  it('团队那一格读不到时说读不到，不说「没有团队设了应用」，并能再读', async () => {
    feishuChoices
      .mockRejectedValueOnce(new Error('网络断了'))
      .mockResolvedValue({ teams: [PERSONAL, TEAM], redirect_uri: 'r' })
    await mount()

    expect(await screen.findByText(/网络断了/)).toBeTruthy()
    expect(screen.queryByText(/你所在的团队都还没设置飞书应用/)).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('cli_team · 还没连接')).toBeTruthy()
  })

  it('较早的飞书连接提示用团队的飞书应用重新连接', async () => {
    listMyIntegrations.mockResolvedValue({
      data: [
        {
          ...FEISHU_ROW,
          config: { app_id: 'cli_old', domain: 'feishu' },
          user_authorized: true,
          reconnect: 'team_app',
        },
      ],
    })
    await mount()
    expect(await screen.findByText(/用团队的飞书应用重新连接/)).toBeTruthy()
  })
})

describe('「连接飞书」那一格跟着检查走', () => {
  it('测试连接查出授权失效，团队那一格也跟着说失效，不再说已连接', async () => {
    const connected = { ...TEAM, connection: { id: 'feishu-own', status: 'ok', user_authorized: true } }
    const lapsed = { ...TEAM, connection: { id: 'feishu-own', status: 'auth_failed', user_authorized: true } }
    listMyIntegrations.mockResolvedValue({ data: [{ ...FEISHU_ROW, user_authorized: true }] })
    feishuChoices
      .mockResolvedValueOnce({ teams: [PERSONAL, connected], redirect_uri: 'r' })
      .mockResolvedValue({ teams: [PERSONAL, lapsed], redirect_uri: 'r' })
    checkIntegration.mockResolvedValue({ ...FEISHU_ROW, status: 'auth_failed', last_error: '授权已过期' })
    await mount()

    expect(await screen.findByText('cli_team · 已连接')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    expect(await screen.findByText('cli_team · 授权已失效，请重新连接')).toBeTruthy()
    expect(screen.queryByText('cli_team · 已连接')).toBeNull()
  })
})

describe('平台应用那时候的旧连接', () => {
  it('没有应用可授权：不给授权按钮，只说用团队的飞书应用重新连接', async () => {
    listMyIntegrations.mockResolvedValue({
      data: [
        {
          ...FEISHU_ROW,
          config: {},
          status: 'auth_failed',
          last_error: '这个连接要改用团队的飞书应用',
          reconnect: 'team_app',
        },
      ],
    })
    await mount()

    expect(await screen.findByText(/用团队的飞书应用重新连接/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /授权个人账号/ })).toBeNull()
  })

  it('带着自己应用的旧连接还能在原地重新授权', async () => {
    listMyIntegrations.mockResolvedValue({
      data: [{ ...FEISHU_ROW, config: { app_id: 'cli_old' }, user_authorized: true, reconnect: 'team_app' }],
    })
    await mount()

    expect(await screen.findByRole('button', { name: '重新授权个人账号' })).toBeTruthy()
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

  it('授权期间团队换了应用，说这次没保存、请重新连接', async () => {
    await mount('/?feishu=app_changed')
    await vi.waitFor(() =>
      expect(sonner.error).toHaveBeenCalledWith('授权期间团队换了飞书应用，这次授权没有保存，请重新连接')
    )
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
