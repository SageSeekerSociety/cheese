// 对话里 AI 说的每一句话上写的名字。
//
// 一个项目可以有好几个 AI 队友，一个房间随时能换。这里的名字曾经是一个写死的
// 「芝士」——换完队友，名册上改了、气泡上没改，看起来就是「换人根本没生效」，
// 而屏幕上没有任何东西说明这两个名字为什么不一样。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

const Panel = ChatPanel as unknown as Component

const SEAT = 'cheese-t1'

function topicOf(id: string): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: id,
    kind: 'topic',
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  } as Topic
}

function aiMsg(id: string, content = id): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'message',
    author_type: 'ai',
    author: SEAT,
    content,
    reply_to: null,
    refs: [],
    created_at: new Date().toISOString(),
  } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []
/** 名册上芝士那一行的名字 —— 后端把它解析成这个房间当前的队友。 */
let agentName = '芝士'

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  history = []
  agentName = '芝士'
  localStorage.setItem('cheesex.me', JSON.stringify({ id: '1', handle: 'me', name: 'me', token: '' }))
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string) => ({
    ok: true,
    status: 200,
    json: async () => {
      const u = String(url)
      if (u.includes('/members'))
        return {
          code: 200,
          data: {
            data: [
              { id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false },
              { id: '2', member_handle: SEAT, name: agentName, role: 'member', agent: true },
            ],
            total: 2,
          },
        }
      if (u.includes('/progress')) return { code: 200, data: { items: [], updated_at: null } }
      if (u.includes('/tasks')) return { code: 200, data: { data: [], total: 0 } }
      return { code: 200, data: { data: history, total: history.length, has_more: false } }
    },
  }))
})

const settle = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

describe('AI 说的话署谁的名', () => {
  it('署这个房间现在交给的那个队友，不是写死的「芝士」', async () => {
    agentName = '评审'
    history = [aiMsg('a', '看过了')]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()

    const names = Array.from(container.querySelectorAll('.im-name')).map((n) => n.textContent?.trim())
    expect(names).toContain('评审')
    expect(names).not.toContain('芝士')
  })

  it('换完队友，名字跟着换 —— 靠的是重新拉一次名册', async () => {
    history = [aiMsg('a', '看过了')]
    const { container, rerender } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true, rosterRevision: 0 },
      global: { plugins: [vuetify] },
    })
    await settle()
    expect(Array.from(container.querySelectorAll('.im-name')).map((n) => n.textContent?.trim())).toContain('芝士')

    // 换人：名册那一行改名了，上面的人把计数 +1 告诉这一栏「过期了」。
    agentName = '评审'
    await rerender({ topic: topicOf('t1'), showComposer: true, rosterRevision: 1 })
    await settle()

    const names = Array.from(container.querySelectorAll('.im-name')).map((n) => n.textContent?.trim())
    expect(names).toContain('评审')
    expect(names).not.toContain('芝士')
  })

  it('名册还没到，也不能空着 —— 退回「芝士」', async () => {
    vi.stubGlobal('fetch', async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => {
        const u = String(url)
        if (u.includes('/members')) throw new Error('boom')
        if (u.includes('/progress')) return { code: 200, data: { items: [], updated_at: null } }
        if (u.includes('/tasks')) return { code: 200, data: { data: [], total: 0 } }
        return { code: 200, data: { data: history, total: history.length, has_more: false } }
      },
    }))
    history = [aiMsg('a', '看过了')]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()
    expect(Array.from(container.querySelectorAll('.im-name')).map((n) => n.textContent?.trim())).toContain('芝士')
  })
})
