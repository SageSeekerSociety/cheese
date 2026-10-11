/** 项目设置「MCP 服务器」：只吃 props、只往上发事件（A 级）。
 *
 *  取数、连接/断开/填密钥、以及授权回来的那一条回跳在
 *  `composables/useProjectMcpSettings.ts`（那里另有一份行为测试）。这里给清单、看点出来
 *  的样子，点一下看报上去的动作。
 */
import type { Plugin } from 'vue'
import type { McpServer, McpServerList } from '../api'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import i18n, { setLocale } from '../i18n'

import ProjectMcpSettings from './ProjectMcpSettings.vue'

import { userRefRoute } from '@/lib/userRef'
import { USER_REF_DIRECTORY } from '@/lib/userRefDirectory'

const tracker: McpServer = {
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

const search: McpServer = {
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

// 句子里那颗人 chip 的名字和去处来自外壳注入的目录：这里注入一个替身，名册空着、去处是
// 这个人在项目里的成员页。
const directory: Plugin = {
  install(app) {
    app.provide(USER_REF_DIRECTORY, {
      name: () => null,
      target: (handle: string) => userRefRoute(handle, 'p'),
      navigate: () => {},
    })
  },
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

async function mount(
  list: McpServerList | null,
  props: { error?: string; busy?: string; values?: Record<string, string> } = {}
) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/projects/:projectId/settings', component: { template: '<div />' } }],
  })
  await router.push('/projects/p/settings')
  return render(ProjectMcpSettings, {
    props: { list, error: '', busy: '', values: {}, ...props },
    global: { plugins: [createVuetify({ components, directives }), router, i18n, directory] },
  })
}

it('a connected server says whose account it acts as, and any member can disconnect it', async () => {
  const view = await mount({
    servers: [{ ...tracker, status: 'connected', authorized_by: 'alice', authorized_at: new Date().toISOString() }],
    problem: null,
  })
  // 授权人是一颗 @chip：和对话里 @ 到他长得一样，点了去他的成员页。
  await waitFor(() => expect(view.container.textContent).toMatch(/由\s*@alice\s*授权/))
  expect(view.container.querySelector('.mcp-row__state .mention')?.getAttribute('data-handle')).toBe('alice')
  await fireEvent.click(view.getByRole('button', { name: '断开' }))
  expect(view.emitted('disconnect')).toEqual([
    [{ ...tracker, status: 'connected', authorized_by: 'alice', authorized_at: expect.any(String) }],
  ])
})

it('draws the value it is given (the layer above decides when to clear it)', async () => {
  const view = await mount({ servers: [search], problem: null }, { values: { SEARCH_KEY: 'sk-123' } })
  const field = (await view.findByLabelText('SEARCH_KEY')) as HTMLInputElement
  expect(field.type).toBe('password')
  expect(field.value).toBe('sk-123')
})

it('reports a typed secret upward on every keystroke', async () => {
  const view = await mount({ servers: [search], problem: null })
  const field = await view.findByLabelText('SEARCH_KEY')
  await fireEvent.update(field, 'sk-123')
  expect(view.emitted('update:value')).toEqual([[{ name: 'SEARCH_KEY', value: 'sk-123' }]])
})

it('offers to save a filled secret and clear a set one', async () => {
  const view = await mount({
    servers: [{ ...search, variables: [{ name: 'SEARCH_KEY', set: true, updated_by: null, updated_at: null }] }],
    problem: null,
  })
  // 已经填过的那一行：空着草稿也能清掉。
  await fireEvent.click(await view.findByRole('button', { name: '清除' }))
  expect(view.emitted('clear')).toEqual([['SEARCH_KEY']])
})

it('reports a save upward only when there is something to send', async () => {
  const view = await mount({ servers: [search], problem: null }, { values: { SEARCH_KEY: 'sk-123' } })
  await fireEvent.click(await view.findByRole('button', { name: '保存' }))
  expect(view.emitted('save')).toEqual([['SEARCH_KEY']])
})

it('marks the row whose action is on its way', async () => {
  const view = await mount({ servers: [tracker, search], problem: null }, { busy: 'tracker' })
  const connect = await view.findByRole('button', { name: '连接' })
  expect(connect.classList.contains('v-btn--loading')).toBe(true)
})

it('labels each server with where it comes from: the .mcp.json, or the types that declare it', async () => {
  const view = await mount({
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

  setLocale('en')
  await waitFor(() => expect(view.container.textContent).toContain('Declared by the 代码评审 type'))
  expect(view.container.textContent).toContain('Declared by the 代码评审, 写作 types')
  expect(view.container.textContent).toContain("From the project's .mcp.json")
})

it('says the list is unreadable or invalid instead of showing servers', async () => {
  const invalid = await mount({ servers: [], problem: 'invalid' })
  expect(invalid.getByText(/格式不正确/)).toBeTruthy()

  const unreadable = await mount({ servers: [], problem: 'unreadable' })
  expect(unreadable.getByText(/稍后重试/)).toBeTruthy()
})

it('says why when there was no answer', async () => {
  const view = await mount(null, { error: '读不出来了' })
  expect(view.getByText('读不出来了')).toBeTruthy()
})
