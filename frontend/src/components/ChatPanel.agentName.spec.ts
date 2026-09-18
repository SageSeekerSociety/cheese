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

import { setLocale } from '@/i18n'

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
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
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

// 默认语言是 en（happy-dom 的 navigator.language 是 en-US），而这份用例断言的是
// 中文界面的字。先把语言钉住，别让它跟着环境飘。
beforeEach(() => setLocale('zh-CN'))
describe('AI 说的话署谁的名', () => {
  it('名册晚到时，已有消息里的点名也更新为显示名', async () => {
    const originalFetch = globalThis.fetch
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.stubGlobal('fetch', async (...args: Parameters<typeof fetch>) => {
      if (String(args[0]).includes('/members')) await gate
      return originalFetch(...args)
    })
    history = [{ ...aiMsg('mention-late', `<@${SEAT}> 请整理方案`), author_type: 'human', author: 'me' }]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: false },
      global: { plugins: [vuetify] },
    })
    await settle()
    expect(container.querySelector('.im-text .mention')?.textContent).toBe(`@${SEAT}`)
    release()
    await settle()
    expect(container.querySelector('.im-text .mention')?.textContent).toBe('@芝士')
  })

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

  it('loads the new room roster when navigating between topics', async () => {
    history = [aiMsg('a', '看过了')]
    const { container, rerender } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()
    expect(Array.from(container.querySelectorAll('.im-name')).map((n) => n.textContent?.trim())).toContain('芝士')

    agentName = '评审'
    await rerender({ topic: topicOf('t2'), showComposer: true })
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
