// 私聊要等两次：先拿到这个会话，再拿它的历史。
//
// 加载态只画一次，而且画在第二次那里。头和输入框是对话栏带进来的，所以画在外面
// 那一层的骨架站的是头将要占的位置——对话栏一到就把它顶下去，正是骨架本该消灭的
// 那种跳动。这一份钉的就是这个分工：外层不画，内层画，而且画在消息真正会出现的
// 地方（头已经在它上面了）。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getPrivateChat = vi.fn()
const listProjectAgents = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getPrivateChat: (...a: unknown[]) => getPrivateChat(...a),
    listProjectAgents: (...a: unknown[]) => listProjectAgents(...a),
  }
})

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    members: [],
    topics: [],
    markDmRead: vi.fn(),
    refreshTopics: vi.fn(),
    refreshUnread: vi.fn(),
    upgradeMessage: vi.fn(),
  }),
}))

import DmView from './DmView.vue'

const Dm = DmView as unknown as Component

const dmTopic = {
  id: 'dm-1',
  project_id: 'p1',
  parent_id: null,
  title: '私聊 · me · cheese',
  kind: 'topic',
  status: 'active',
  created_by: 'me',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
} as Topic

function pending<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

/** 历史一直不回来，这样「正在等历史」那一刻是拿得住的。 */
function stubChatBackend() {
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string) => {
    const u = String(url)
    const body = (data: unknown) => ({ ok: true, status: 200, json: async () => ({ code: 200, data }) })
    if (u.includes('/members')) return body({ data: [], total: 0 })
    if (u.includes('/progress')) return body({ items: [], updated_at: null })
    if (u.includes('/tasks')) return body({ data: [], total: 0 })
    // 历史：永远不回来。
    return new Promise(() => {})
  })
}

beforeEach(() => {
  getPrivateChat.mockReset()
  listProjectAgents.mockReset().mockResolvedValue({
    data: [{ handle: 'reviewer', display_name: '评审', is_default: false, is_active: true }],
  })
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
  stubChatBackend()
})

function open(peer = 'agent:reviewer') {
  return render(Dm, { props: { projectId: 'p1', peer }, global: { plugins: [vuetify] } })
}

const settle = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

describe('私聊的加载态只有一层', () => {
  it('取会话的那一步不画骨架 —— 那个位置是头的，不是消息的', async () => {
    const gate = pending<Topic>()
    getPrivateChat.mockReturnValue(gate.promise)
    const { container } = open()
    await settle()

    expect(container.querySelector('.pr-header'), '会话还没到，对话栏当然还没有').toBeNull()
    expect(container.querySelector('[role="status"][aria-busy="true"]'), '这一步画的骨架会被头顶下去').toBeNull()

    gate.resolve(dmTopic)
    await waitFor(() => expect(container.querySelector('.pr-header')).not.toBeNull())
  })

  it('会话一到，骨架就在消息该出现的地方接手 —— 头已经在它上面了', async () => {
    getPrivateChat.mockResolvedValue(dmTopic)
    const { container } = open()

    await waitFor(() => expect(container.querySelector('.messages [role="status"][aria-busy="true"]')).not.toBeNull())
    expect(container.querySelector('.pr-header'), '骨架上面得是头，不是骨架自己占着头的位置').not.toBeNull()
    expect(container.querySelectorAll('[role="status"][aria-busy="true"]').length, '一次等待只画一处').toBe(1)
  })
})

// 地址栏里那一段既是「我在跟谁说话」，也是未读表的键。人用自己的 handle，队友用
// `agent:<handle>` —— 队友的名字是每个项目自己起的，不加前缀就会和同名的人撞成
// 一间对话。
describe('私聊的地址说的是跟谁', () => {
  it('agent: 开头 → 问的是这个队友，不是一个叫这个名字的人', async () => {
    getPrivateChat.mockResolvedValue(dmTopic)
    open('agent:reviewer')
    await waitFor(() => expect(getPrivateChat).toHaveBeenCalled())
    expect(getPrivateChat).toHaveBeenCalledWith('p1', 'me', undefined, 'reviewer')
  })

  it('没有前缀 → 那是个人，队友那一栏空着', async () => {
    getPrivateChat.mockResolvedValue(dmTopic)
    open('ligan')
    await waitFor(() => expect(getPrivateChat).toHaveBeenCalled())
    expect(getPrivateChat).toHaveBeenCalledWith('p1', 'me', 'ligan', undefined)
  })

  it('头上写的是这个队友自己的名字 —— 项目里有好几个，都叫「芝士」就是句假话', async () => {
    getPrivateChat.mockResolvedValue(dmTopic)
    const { findByText } = open('agent:reviewer')
    expect(await findByText('评审')).toBeTruthy()
  })

  it('队友名单拿不到也照样能聊 —— 标题退回 handle', async () => {
    getPrivateChat.mockResolvedValue(dmTopic)
    listProjectAgents.mockRejectedValue(new Error('boom'))
    const { findByText } = open('agent:reviewer')
    expect(await findByText('reviewer')).toBeTruthy()
  })
})
