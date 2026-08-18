// 输入框里的东西属于它被打出来的那个话题。
//
// 切话题时输入框的状态整个留在原地，其中 replyTarget 指向的是**上一个话题**的
// 块——屏幕上看不出异常（本话题找不到父块就不画引用条），落库的 reply_to 却已经
// 跨话题了，而会话树是记忆和摘要重建的依据之一。
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

describe('输入框的内容属于它被打出来的那个话题', () => {
  it('切走再回来，草稿还在；切到别的话题，输入框是空的', async () => {
    const { container, rerender } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()

    const textarea = container.querySelector('textarea') as HTMLTextAreaElement
    await fireEvent.update(textarea, '这句话是说给 t1 的')

    await rerender({ topic: topicOf('t2'), showComposer: true })
    await settle()
    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('')

    await rerender({ topic: topicOf('t1'), showComposer: true })
    await settle()
    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('这句话是说给 t1 的')
  })

  it('回复目标不跟着你换话题——它指的是上一个话题里的块', async () => {
    history = [msg('m1', 'other', daysAgo(0), '这条被回复')]
    const { container, rerender } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify] },
    })
    await settle()

    const replyBtn = Array.from(container.querySelectorAll('.im-act')).find(
      (b) => b.getAttribute('title') === '回复'
    ) as HTMLButtonElement
    await fireEvent.click(replyBtn)
    expect(container.querySelector('.reply-bar')).toBeTruthy()

    await rerender({ topic: topicOf('t2'), showComposer: true })
    await settle()
    // 留着的话，下一条发到 t2 的消息会带上 t1 的 reply_to。
    expect(container.querySelector('.reply-bar')).toBeNull()
  })
})
