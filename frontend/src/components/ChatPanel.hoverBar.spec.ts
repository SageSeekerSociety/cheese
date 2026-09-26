// 消息的悬停条：整列只有一个，跟着指针走，按下去作用在指针所在的那条消息上。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

const Panel = ChatPanel as unknown as Component
let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []

function msg(id: string, content: string): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'message',
    author_type: 'participant',
    author: 'other',
    content,
    reply_to: null,
    refs: [],
    created_at: new Date().toISOString(),
  } as unknown as Block
}

function event(id: string, content: string): Block {
  return { ...msg(id, content), kind: 'event', author_type: 'platform', author: 'system' } as Block
}

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string) => ({
    ok: true,
    status: 200,
    json: async () =>
      String(url).includes('/progress')
        ? { code: 200, data: { items: [], updated_at: null } }
        : String(url).includes('/tasks')
          ? { code: 200, data: { data: [], total: 0 } }
          : { code: 200, data: { data: history, total: history.length, has_more: false } },
  }))
})

const settle = () => new Promise((r) => setTimeout(r, 0))

async function mountRoom(blocks: Block[]) {
  history = blocks
  const view = render(Panel, {
    props: { topic: { id: 't1', project_id: 'p1', title: 't1', kind: 'topic' } as Topic, showComposer: true },
    global: { plugins: [vuetify] },
  })
  await settle()
  return view
}

const replyButtons = (container: Element) =>
  Array.from(container.querySelectorAll('button')).filter((b) => b.getAttribute('title') === '回复')

async function pointAt(container: Element, selector: string) {
  await fireEvent.mouseOver(container.querySelector(selector)!)
}

describe('消息的悬停条', () => {
  it('回复的是指针所在的那一条', async () => {
    const { container } = await mountRoom([msg('m1', '第一条'), msg('m2', '第二条')])

    await pointAt(container, '[data-mid="m2"] .im-text')
    await fireEvent.click(replyButtons(container)[0])
    expect(container.querySelector('.reply-chip')?.textContent).toContain('第二条')

    await pointAt(container, '[data-mid="m1"] .im-text')
    await fireEvent.click(replyButtons(container)[0])
    expect(container.querySelector('.reply-chip')?.textContent).toContain('第一条')
  })

  it('整列只有一个，不是每条消息各带一个', async () => {
    const { container } = await mountRoom([msg('m1', 'a'), msg('m2', 'b'), msg('m3', 'c')])
    await pointAt(container, '[data-mid="m2"] .im-text')
    expect(replyButtons(container)).toHaveLength(1)
  })

  it('指针移到事件行上就收起——事件行没有这些操作', async () => {
    const { container } = await mountRoom([msg('m1', 'a'), event('e1', '话题已归档')])
    await pointAt(container, '[data-mid="m1"] .im-text')
    const bar = container.querySelector('.hover-bar')!
    expect(bar.getAttribute('aria-hidden')).toBe('false')
    await pointAt(container, '.im-event')
    expect(bar.getAttribute('aria-hidden')).toBe('true')
  })

  it('移出整列就收起', async () => {
    const { container, getByTestId } = await mountRoom([msg('m1', 'a')])
    await pointAt(container, '[data-mid="m1"] .im-text')
    await fireEvent.mouseLeave(getByTestId('chat-scroll'))
    expect(container.querySelector('.hover-bar')!.getAttribute('aria-hidden')).toBe('true')
  })
})
