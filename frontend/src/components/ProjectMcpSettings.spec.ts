/** 项目设置「MCP 服务器」：连接属于项目，谁授权的看得见，密钥只进不出。 */
import type { Plugin } from 'vue'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getMcpServers: vi.fn(),
  connectMcpServer: vi.fn(),
  disconnectMcpServer: vi.fn(),
  setMcpSecret: vi.fn(),
  clearMcpSecret: vi.fn(),
}))
vi.mock('../api', () => api)

// 一次性动作的结果走全局 toast（§3.11）：这里只验它说了什么。
const sonner = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: { success: sonner.success, error: sonner.error } }))

import i18n, { setLocale } from '../i18n'

import ProjectMcpSettings from './ProjectMcpSettings.vue'

import { userRefRoute } from '@/lib/userRef'
import { USER_REF_DIRECTORY } from '@/lib/userRefDirectory'

const tracker = {
  name: 'tracker',
  transport: 'http',
  host: 'mcp.example.test',
  auth: 'oauth',
  status: 'disconnected',
  declared_by: null,
  authorized_by: null,
  authorized_at: null,
  variables: [],
}
const search = {
  name: 'search',
  transport: 'http',
  host: 'search.example.test',
  auth: 'headers',
  status: 'missing_values',
  declared_by: null,
  authorized_by: null,
  authorized_at: null,
  variables: [{ name: 'SEARCH_KEY', set: false, updated_by: null, updated_at: null }],
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.resetAllMocks()
  api.getMcpServers.mockResolvedValue({ servers: [tracker, search], problem: null })
})
afterEach(() => cleanup())

// 句子里的人名 chip（「由 @某人 授权」）的名字和去处来自外壳注入的目录
// （lib/userRefDirectory.ts；外壳那份在 composables/useUserRefDirectory.ts）。这一页
// 单独挂起来时外壳不在，注入一个替身：名册空着，chip 退回画 `@alice`；去处是这个人
// 在项目里的成员页——下面那条断言要的正是它画成了可点的那一支（没去处时 chip 画成
// 一个死的 @名字，没有 data-handle）。
const directory: Plugin = {
  install(app) {
    app.provide(USER_REF_DIRECTORY, {
      name: () => null,
      target: (handle: string) => userRefRoute(handle, 'p'),
      navigate: () => {},
    })
  },
}

async function mount(url = '/projects/p/settings') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/projects/:projectId/settings', component: { template: '<div />' } }],
  })
  await router.push(url)
  const view = render(ProjectMcpSettings, {
    props: { projectId: 'p' },
    global: { plugins: [createVuetify({ components, directives }), router, i18n, directory] },
  })
  return { view, router }
}

it('connecting sends the browser to the authorization server', async () => {
  const assign = vi.fn()
  vi.stubGlobal('location', { ...window.location, assign })
  api.connectMcpServer.mockResolvedValue({ authorization_url: 'https://as.example.test/authorize?x=1' })
  const { view } = await mount()
  await fireEvent.click(await view.findByRole('button', { name: '连接' }))
  await waitFor(() => expect(assign).toHaveBeenCalledWith('https://as.example.test/authorize?x=1'))
  expect(api.connectMcpServer).toHaveBeenCalledWith('p', 'tracker')
  vi.unstubAllGlobals()
})

it('a connected server says whose account it acts as, and any member can disconnect it', async () => {
  api.getMcpServers.mockResolvedValue({
    servers: [{ ...tracker, status: 'connected', authorized_by: 'alice', authorized_at: new Date().toISOString() }],
    problem: null,
  })
  api.disconnectMcpServer.mockResolvedValue(null)
  const { view } = await mount()
  // 授权人是一颗 @chip：和对话里 @ 到他长得一样，点了去他的成员页。
  await waitFor(() => expect(view.container.textContent).toMatch(/由\s*@alice\s*授权/))
  expect(view.container.querySelector('.mcp-row__state .mention')?.getAttribute('data-handle')).toBe('alice')
  await fireEvent.click(view.getByRole('button', { name: '断开' }))
  await waitFor(() => expect(api.disconnectMcpServer).toHaveBeenCalledWith('p', 'tracker'))
})

it('a secret value is sent once and never shown again', async () => {
  api.setMcpSecret.mockResolvedValue(null)
  const { view } = await mount()
  const field = (await view.findByLabelText('SEARCH_KEY')) as HTMLInputElement
  expect(field.type).toBe('password')
  await fireEvent.update(field, 'sk-123')
  await fireEvent.click(view.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(api.setMcpSecret).toHaveBeenCalledWith('p', 'SEARCH_KEY', 'sk-123'))
  await waitFor(() => expect(field.value).toBe(''))
})

function failedLanding(key: string, params?: Record<string, unknown>) {
  const query = new URLSearchParams({ mcp: 'tracker', mcp_error: key })
  if (params) query.set('mcp_error_params', JSON.stringify(params))
  return `/projects/p/settings?${query}`
}

it('the result of an authorization is said once and taken off the address', async () => {
  const { router } = await mount(failedLanding('mcpAuthIncomplete', { error: 'access_denied' }))
  await waitFor(() => expect(sonner.error).toHaveBeenCalledWith('连接 tracker 失败：授权没有完成：access_denied'))
  await waitFor(() => expect(router.currentRoute.value.query).toEqual({}))
})

it('labels each server with where it comes from: the .mcp.json, or the types that declare it', async () => {
  api.getMcpServers.mockResolvedValue({
    servers: [
      tracker,
      { ...tracker, name: 'ticket', declared_by: [{ name: 'code-review', title: '代码评审' }] },
      {
        ...tracker,
        name: 'docs',
        declared_by: [
          { name: 'code-review', title: '代码评审' },
          { name: 'writer', title: '写作' },
        ],
      },
    ],
    problem: null,
  })
  const { view } = await mount()
  const source = async (name: string) => {
    await view.findByText(name)
    return view.container.querySelector(`[data-server="${name}"] [data-testid="mcp-source"]`)?.textContent
  }
  expect(await source('tracker')).toBe('来自项目的 .mcp.json')
  expect(await source('ticket')).toBe('由 代码评审 类型声明')
  expect(await source('docs')).toBe('由 代码评审、写作 类型声明')
  // The line is in the UI's own font; only the file name is code.
  const line = (name: string) => view.container.querySelector(`[data-server="${name}"] [data-testid="mcp-source"]`)!
  expect(line('tracker').querySelector('code')?.textContent).toBe('.mcp.json')
  expect(line('ticket').querySelector('code')).toBeNull()
  expect(line('ticket').classList.contains('t-meta')).toBe(false)

  setLocale('en')
  await waitFor(() => expect(view.container.textContent).toContain('Declared by the 代码评审 type'))
  expect(view.container.textContent).toContain('Declared by the 代码评审, 写作 types')
  expect(view.container.textContent).toContain("From the project's .mcp.json")
})

it('in English the reason a connection failed is said in English', async () => {
  setLocale('en')
  await mount(failedLanding('mcpAuthIncomplete', { error: { key: 'mcpNoAuthCode', params: {} } }))
  await waitFor(() =>
    expect(sonner.error).toHaveBeenCalledWith(
      "Failed to connect tracker: Authorization didn't complete: no authorization code was received"
    )
  )
})

it('a code this build does not know says only that connecting failed', async () => {
  setLocale('en')
  await mount(failedLanding('failed'))
  await waitFor(() => expect(sonner.error).toHaveBeenCalledWith('Failed to connect tracker'))
})
