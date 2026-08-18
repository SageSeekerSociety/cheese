// 时间线上的两条刻度。
//
// 它们是同一个缺口的两半：一个离开一周回来的人，在这一列里恢复不了上下文。
// 时间戳只有 HH:mm，一串 09:32 / 14:07 分不出哪条是今天的；侧栏的未读角标只
// 回答「有没有新的」，不回答「新的从哪开始」。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

const Panel = ChatPanel as unknown as Component

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

function msg(id: string, author: string, at: Date, content = id): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'message',
    author_type: author === 'cheese' ? 'ai' : 'human',
    author,
    content,
    reply_to: null,
    refs: [],
    created_at: at.toISOString(),
  } as unknown as Block
}

const daysAgo = (n: number, h = 9) => {
  const d = new Date()
  d.setDate(d.getDate() - n)
  d.setHours(h, 30, 0, 0)
  return d
}

let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  history = []
  // 未读只数「别人发的」，所以这些测试必须有一个真实的自己。没有它，
  // myHandle() 返回空串，那条过滤永远不成立，测试会因为别的原因通过。
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
    json: async () =>
      String(url).includes('/progress')
        ? { code: 200, data: { items: [], updated_at: null } }
        : { code: 200, data: { data: history, total: history.length, has_more: false } },
  }))
})

const settle = () => new Promise((r) => setTimeout(r, 0))

describe('日期分隔线', () => {
  it('跨天的地方标一次，同一天的连续消息不重复标', async () => {
    history = [msg('a', 'other', daysAgo(2)), msg('b', 'other', daysAgo(2, 14)), msg('c', 'other', daysAgo(0))]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()

    const labels = Array.from(container.querySelectorAll('.tl-mark')).map((n) => n.textContent?.trim())
    // 两天 → 两条线（最老的那条也要标：翻到顶部的人一样需要知道这是什么时候）。
    expect(labels).toHaveLength(2)
    expect(labels[1]).toBe('今天')
  })

  it('时间戳本身分不出日期，所以这条线是唯一的信息来源', async () => {
    history = [msg('a', 'other', daysAgo(1))]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()
    expect(container.querySelector('.tl-mark')?.textContent?.trim()).toBe('昨天')
  })
})

describe('新消息分隔线', () => {
  it('按开话题那一刻的未读数往回数，标在第一条没读过的消息上', async () => {
    history = [
      msg('old', 'other', daysAgo(0, 9)),
      msg('unread-1', 'other', daysAgo(0, 10)),
      msg('unread-2', 'other', daysAgo(0, 11)),
    ]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true, unreadOnOpen: 2 },
      global: { plugins: [vuetify] },
    })
    await settle()

    const unread = container.querySelector('.tl-mark--unread')
    expect(unread).toBeTruthy()
    // 线画在 unread-1 之前：它后面跟着的第一条消息就是没读过的第一条。
    const next = unread!.nextElementSibling
    expect(next?.getAttribute('data-mid')).toBe('unread-1')
  })

  it('没有未读就不画线', async () => {
    history = [msg('a', 'other', daysAgo(0))]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true, unreadOnOpen: 0 },
      global: { plugins: [vuetify] },
    })
    await settle()
    expect(container.querySelector('.tl-mark--unread')).toBeNull()
  })

  it('自己发的消息不算未读——线不会因为自己刚说过话而错位', async () => {
    // 自己那条排在最后：不过滤作者的话，线会错标在 mine 上。
    history = [msg('theirs', 'other', daysAgo(0, 9)), msg('mine', 'me', daysAgo(0, 10))]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true, unreadOnOpen: 1 },
      global: { plugins: [vuetify] },
    })
    await settle()
    expect(container.querySelector('.tl-mark--unread')?.nextElementSibling?.getAttribute('data-mid')).toBe('theirs')
  })
})
