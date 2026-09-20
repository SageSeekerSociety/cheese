// 忘了 @ 的那一下。
//
// 房间里最后一句话是对着人说的，芝士就不会动 —— 这是对的（没 @ 不等于没说，那
// 条消息在待读窗口里等着下一轮捎上）。伤人的是房间里**没有任何东西说明这一点**：
// 一个人贴完需求等了八分钟，追问「你有看到我的问题嘛」，全程没人接、也没有一行
// 字告诉他为什么。这个文件钉的就是那一行字，和它后面那一次点击。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
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

function humanMsg(id: string, content: string): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'message',
    author_type: 'human',
    author: 'me',
    content,
    reply_to: null,
    refs: [],
    created_at: new Date().toISOString(),
  } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []
let posts: string[] = []
let started = true

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  history = []
  posts = []
  started = true
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: '我' }))
  vi.stubGlobal(
    'WebSocket',
    class {
      static OPEN = 1
      readyState = 1
      onopen: (() => void) | null = null
      constructor() {
        setTimeout(() => this.onopen?.(), 0)
      }
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url)
    if (init?.method === 'POST') posts.push(u)
    return {
      ok: true,
      status: 200,
      json: async () => {
        if (u.includes('/summon')) return { code: 200, data: { started } }
        if (u.includes('/members'))
          return {
            code: 200,
            data: {
              data: [
                { id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false },
                { id: '2', member_handle: SEAT, name: '芝士', role: 'member', agent: true },
              ],
              total: 2,
            },
          }
        if (u.includes('/progress')) return { code: 200, data: { items: [], updated_at: null } }
        if (u.includes('/tasks')) return { code: 200, data: { data: [], total: 0 } }
        return { code: 200, data: { data: history, total: history.length, has_more: false } }
      },
    }
  })
})

const settle = async () => {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function mount(topicId = 't1') {
  return render(Panel, {
    props: { topic: topicOf(topicId), showComposer: true },
    global: { plugins: [vuetify] },
  })
}

describe('没叫芝士的那条消息', () => {
  it('房间里最后一句没 @ 它，就说出来，并给出一次点击', async () => {
    history = [humanMsg('h1', '这个分页方案你看下')]
    const { container } = mount()
    await settle()

    const hint = container.querySelector('.summon-hint')
    expect(hint, '最后一句没人接，房间里却没有任何东西说明为什么').toBeTruthy()
    expect(hint!.textContent).toContain('没叫芝士')
  })

  it('@ 了它的那条消息不提示——它本来就会动', async () => {
    history = [humanMsg('h1', `<@${SEAT}> 这个分页方案你看下`)]
    const { container } = mount('t-mentioned')
    await settle()

    expect(container.querySelector('.summon-hint')).toBeNull()
  })

  // 一次发送落成两块：一句话 + 一张图。@ 写在那句话里，而排在最后的是图片块，
  // 它的正文是一个文件路径——只看最后一块，配图的每一次召唤都会被判成「没叫」。
  it('一句 @ 了它的话配一张图，不提示', async () => {
    history = [
      humanMsg('h1', `<@${SEAT}> 这个分页方案你看下`),
      { ...humanMsg('h2', 'topic-a/shot.png'), kind: 'attachment', mime_type: 'image/png' } as Block,
    ]
    const { container } = mount('t-with-image')
    await settle()

    expect(container.querySelector('.summon-hint')).toBeNull()
  })

  it('点一下就叫它来读，且不再多发一条一模一样的消息', async () => {
    history = [humanMsg('h1', '这个分页方案你看下')]
    const { container, getByRole } = mount('t-click')
    await settle()

    await fireEvent.click(getByRole('button', { name: '让它现在就看' }))
    await settle()

    expect(posts.filter((u) => u.includes('/summon'))).toHaveLength(1)
    expect(
      posts.some((u) => u.endsWith('/messages')),
      '补救不该再发一条消息'
    ).toBe(false)
    // 叫过了就把提示收起来：点一下什么都不变，看起来和坏掉一模一样。
    expect(container.querySelector('.summon-hint')).toBeNull()
  })

  it('后端说这一下本来就不必（已经在干活），提示照样收起来', async () => {
    started = false
    history = [humanMsg('h1', '这个分页方案你看下')]
    const { container, getByRole } = mount('t-noop')
    await settle()

    await fireEvent.click(getByRole('button', { name: '让它现在就看' }))
    await settle()

    expect(container.querySelector('.summon-hint')).toBeNull()
  })
})
