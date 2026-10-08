// 句子里提到的一个人，和对话消息里 @ 到的那个人，是同一颗 chip：长得一样，点了去
// 同一个地方。这里拿真的路由表，一边点 UserRefLink，一边点对话里渲染出来的 @chip（私聊
// 页挂真的对话栏），比两边落到的地址。
import type { Component } from 'vue'
import type { Router } from 'vue-router'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import userRoutes from '@/router/user'
import { workspaceRoutes } from '@/router/workspaceRoutes'

const listBlocks = vi.fn()
// vi.mock 会被提到文件顶上，引用的东西要用 vi.hoisted 一起提上去。
const { room, members } = vi.hoisted(() => ({
  room: {
    id: 'dm-1',
    project_id: 'p1',
    parent_id: null,
    title: '私聊',
    kind: 'topic',
    status: 'active',
    created_at: '2026-08-15T00:00:00Z',
  },
  members: [] as { user_handle: string; role: string; name: string; agent: boolean }[],
}))

vi.mock('../../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../../lib/libraryApi')>('../../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    members,
    topics: [],
    unreadMap: {},
    markDmRead: vi.fn(),
    refreshTopics: vi.fn(),
    refreshUnread: vi.fn(),
    upgradeMessage: vi.fn(),
  }),
}))

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getPrivateChat: vi.fn().mockResolvedValue(room),
    listProjectAgents: vi.fn().mockResolvedValue({ data: [] }),
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listTopicMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    chatWsUrl: () => 'ws://test/ws',
    attachmentRawUrl: () => '',
  }
})

import { provideUserRefDirectory } from '@/composables/useUserRefDirectory'

import UserRefLink from './UserRefLink.vue'

import DmView from '@/views/workspace/DmView.vue'

const Empty = defineComponent({ render: () => h('div') })

// 真的路由记录，组件换成空壳：要比的是地址，不是那一页画出来什么。
function stripComponents<T>(record: T): T {
  const r = { ...(record as Record<string, unknown>) }
  if ('component' in r) r.component = Empty
  if ('components' in r) r.components = Object.fromEntries(Object.keys(r.components as object).map((k) => [k, Empty]))
  if (Array.isArray(r.children)) r.children = r.children.map(stripComponents)
  return r as T
}

async function makeRouter(at: string): Promise<Router> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [stripComponents(workspaceRoutes), stripComponents(userRoutes), { path: '/', component: Empty }],
  })
  await router.push(at)
  await router.isReady()
  return router
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = class {
    static OPEN = 1
    readyState = 0
    close() {}
    send() {}
  }
})

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
})

const settle = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 外壳在 App.vue 注入真名册；这里在树根上做同一件事。 */
function withDirectory(child: () => ReturnType<typeof h>) {
  return defineComponent({
    setup() {
      provideUserRefDirectory()
      return child
    },
  })
}

function mountRef(router: Router, props: Record<string, unknown>) {
  return render(withDirectory(() => h(UserRefLink, props)) as unknown as Component, {
    global: { plugins: [vuetify, router] },
  })
}

/** 私聊里一条 @ 了 handle 的消息：点它那颗 chip，看私聊页把人带到哪。 */
async function chatMention(handle: string, name: string, agent: boolean): Promise<{ path: string; html: string }> {
  const router = await makeRouter('/projects/p1/dm/me')
  members.splice(0, members.length, { user_handle: handle, role: 'member', name, agent })
  listBlocks.mockResolvedValue({
    data: [
      {
        id: 'b1',
        conversation_id: room.id,
        kind: 'message',
        author_type: 'participant',
        author: 'me',
        content: `看一下 <@${handle}>`,
        created_at: '2026-08-15T09:00:00Z',
      },
    ],
    has_more: false,
  })
  const { container } = render(DmView as unknown as Component, {
    props: { projectId: 'p1', peer: 'me2' },
    global: { plugins: [vuetify, router] },
  })
  const chip = await waitFor(() => {
    const el = container.querySelector(`.mention[data-handle="${handle}"]`)
    expect(el).not.toBeNull()
    return el as HTMLElement
  })
  const html = chip.outerHTML
  await fireEvent.click(chip)
  await waitFor(() => expect(router.currentRoute.value.path).not.toBe('/projects/p1/dm/me'))
  return { path: router.currentRoute.value.path, html }
}

/** 同一个人，句子里的 @chip（UserRefLink）点下去落到哪。 */
async function userRefLandsOn(handle: string, name: string): Promise<string> {
  const router = await makeRouter('/projects/p1/settings')
  const { getByText } = mountRef(router, { handle, name })
  await fireEvent.click(getByText(`@${name}`))
  await waitFor(() => expect(router.currentRoute.value.path).not.toBe('/projects/p1/settings'))
  return router.currentRoute.value.path
}

describe('UserRefLink：句子里的一个人', () => {
  it('画成 @名字，和对话消息里的 @chip 是同一份 markup', async () => {
    const router = await makeRouter('/projects/p1/settings')
    const { container } = mountRef(router, { handle: 'alice', name: '爱丽丝' })
    const chip = container.querySelector('.mention') as HTMLElement
    expect(chip.textContent).toBe('@爱丽丝')

    const holder = document.createElement('div')
    holder.innerHTML = (await chatMention('alice', '爱丽丝', false)).html
    const chat = holder.firstElementChild as HTMLElement
    expect(chip.className).toBe(chat.className)
    expect(chip.dataset.handle).toBe(chat.dataset.handle)
    expect(chip.textContent).toBe(chat.textContent)
  })

  it('点一个人 → 他在项目里的成员页，和对话里点 @他 同一个地址', async () => {
    const fromRef = await userRefLandsOn('alice', '爱丽丝')
    expect(fromRef).toBe('/projects/p1/members/alice')
    expect((await chatMention('alice', '爱丽丝', false)).path).toBe(fromRef)
  })

  it('点一个 AI 队友 → 同样和对话里点 @它 同一个地址', async () => {
    const fromRef = await userRefLandsOn('cheese-3fa2', '芝士')
    expect(fromRef).toBe('/projects/p1/members/cheese-3fa2')
    expect((await chatMention('cheese-3fa2', '芝士', true)).path).toBe(fromRef)
  })

  it('键盘：聚焦后回车也能去', async () => {
    const router = await makeRouter('/projects/p1/settings')
    const { getByText } = mountRef(router, { handle: 'alice', name: '爱丽丝' })
    const chip = getByText('@爱丽丝')
    expect(chip.getAttribute('tabindex')).toBe('0')
    await fireEvent.keyDown(chip, { key: 'Enter' })
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/members/alice'))
  })

  it('项目之外 → 他的个人主页', async () => {
    const router = await makeRouter('/')
    const { getByText } = mountRef(router, { handle: 'alice', name: '爱丽丝' })
    await fireEvent.click(getByText('@爱丽丝'))
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/users/alice'))
  })

  it('点人名不会同时触发所在那一行自己的点击', async () => {
    const router = await makeRouter('/projects/p1/settings')
    const onRow = vi.fn()
    const Row = withDirectory(() => h('div', { onClick: onRow }, [h(UserRefLink, { handle: 'alice', name: '爱丽丝' })]))
    const { getByText } = render(Row, { global: { plugins: [vuetify, router] } })
    await fireEvent.click(getByText('@爱丽丝'))
    expect(onRow).not.toHaveBeenCalled()
  })

  it('只知道名字、不知道 handle：照样是 @名字，但点了哪也不去', async () => {
    const router = await makeRouter('/projects/p1/settings')
    const { getByText } = mountRef(router, { name: '爱丽丝' })
    const chip = getByText('@爱丽丝')
    expect(chip.getAttribute('role')).toBeNull()
    await fireEvent.click(chip)
    await settle()
    expect(router.currentRoute.value.path).toBe('/projects/p1/settings')
  })

  it('没人注入名册（组件目录、单独挂载）：画出 @handle，不能点', async () => {
    const router = await makeRouter('/projects/p1/settings')
    members.splice(0, members.length, { user_handle: 'alice', role: 'member', name: '爱丽丝', agent: false })
    const { getByText } = render(UserRefLink as unknown as Component, {
      props: { handle: 'alice' },
      global: { plugins: [vuetify, router] },
    })
    const chip = getByText('@alice')
    expect(chip.getAttribute('role')).toBeNull()
    await fireEvent.click(chip)
    await settle()
    expect(router.currentRoute.value.path).toBe('/projects/p1/settings')
  })

  it('注入了名册：只给 handle 时名字从项目成员里取', async () => {
    const router = await makeRouter('/projects/p1/settings')
    members.splice(0, members.length, { user_handle: 'alice', role: 'member', name: '爱丽丝', agent: false })
    const { getByText } = mountRef(router, { handle: 'alice' })
    expect(getByText('@爱丽丝')).toBeTruthy()
  })
})
