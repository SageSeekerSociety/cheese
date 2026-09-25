// 回复的那条在输入框里是一枚标签：发出去的消息挂在那一条下面；标签拿掉了，就不再挂。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, within } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import { t } from '@/i18n'

const Panel = ChatPanel as unknown as Component
let vuetify: ReturnType<typeof createVuetify>
const sent: Record<string, unknown>[] = []
const history: Block[] = [
  {
    id: 'm1',
    project_id: 'p1',
    topic_id: 't1',
    kind: 'message',
    author_type: 'participant',
    author: 'other',
    content: 'B 组第 7 行录错了',
    reply_to: null,
    refs: [],
    created_at: new Date().toISOString(),
  } as unknown as Block,
]

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  sent.length = 0
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
      send(payload: string) {
        sent.push(JSON.parse(payload))
      }
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

// 两条用例各发一句不同的话：同一个话题没收到回执的发件箱会跟着话题留下来，
// 下一次打开时照样重发，只认内容才分得清是哪一次发的。
async function replyThenSend(text: string, cancel: boolean) {
  const view = render(Panel, {
    props: { topic: { id: 't1', project_id: 'p1', title: 't1', kind: 'topic' } as Topic, showComposer: true },
    global: { plugins: [vuetify] },
  })
  await settle()
  await fireEvent.mouseOver(view.container.querySelector('[data-mid="m1"] .im-text')!)
  const bar = view.container.querySelector('.hover-bar') as HTMLElement
  await fireEvent.click(within(bar).getByTitle('回复'))
  if (cancel) await fireEvent.click(view.getByRole('button', { name: t('work.room.composer.cancelReply') }))
  const box = view.container.querySelector('textarea') as HTMLTextAreaElement
  box.focus()
  await fireEvent.update(box, text)
  await fireEvent.keyDown(box, { key: 'Enter' })
  await settle()
  view.unmount()
  return sent.find((f) => f.content === text)
}

describe('输入框里的回复标签', () => {
  it('发出去的消息挂在回复的那一条下面', async () => {
    expect((await replyThenSend('按这条改', false))?.reply_to).toBe('m1')
  })

  it('标签拿掉了，发出去的就不再是回复', async () => {
    const frame = await replyThenSend('不回复了', true)
    expect(frame).toBeTruthy()
    expect(frame?.reply_to ?? null).toBeNull()
  })
})
