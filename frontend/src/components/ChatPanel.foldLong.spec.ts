// 人在支线和任务里贴一段需求、报错或长文，一条就占满一栏，上下翻前后的对话很难
// （反馈 FB-85）。那里人发的长消息和队友的长回复一样收起，给「展开 / 收起」；频道
// 主线上人发的消息照旧整条摊开。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

const Panel = ChatPanel as unknown as Component

const ROOM: Topic = {
  id: 'r1',
  project_id: 'p1',
  parent_id: null,
  title: 'r1',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
} as Topic

const LONG = Array.from({ length: 80 }, (_, i) => `报错第 ${i + 1} 行`).join('\n')

function humanMsg(id: string, content: string): Block {
  return {
    id,
    project_id: 'p1',
    conversation_id: 'r1',
    kind: 'message',
    author_type: 'participant',
    author: 'me',
    content,
    reply_to: null,
    refs: [],
    created_at: new Date().toISOString(),
  } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []
const originalScrollHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight')

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
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
          data: { data: [{ id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false }], total: 1 },
        }
      if (u.includes('/progress')) return { code: 200, data: { items: [], updated_at: null } }
      if (u.includes('/tasks')) return { code: 200, data: { data: [], total: 0 } }
      return { code: 200, data: { data: history, total: history.length, has_more: false } }
    },
  }))
  // happy-dom 没有排版：正文的渲染高度和行高都替它报出来——一段 80 行的贴文，
  // 每行 24px，远高过收起的那段阅读高度。
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return this.classList.contains('im-text') ? 80 * 24 : 0
    },
  })
  const realStyle = window.getComputedStyle.bind(window)
  vi.spyOn(window, 'getComputedStyle').mockImplementation((el: Element, pseudo?: string | null) => {
    const style = realStyle(el, pseudo)
    if (!el.classList.contains('im-text')) return style
    return new Proxy(style, {
      get: (target, key) => (key === 'lineHeight' ? '24px' : Reflect.get(target, key)),
    })
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  if (originalScrollHeight) Object.defineProperty(HTMLElement.prototype, 'scrollHeight', originalScrollHeight)
  else delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollHeight
})

const settle = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

describe('自己发的长消息', () => {
  it.each([
    ['任务里的对话', { conversationId: 'task-1' }],
    ['支线', { conversationId: 'th1', inThread: true }],
  ])('%s里收起来，「展开」打开、「收起」收回', async (_where, place) => {
    history = [humanMsg('h1', LONG)]
    const { container } = render(Panel, {
      props: { topic: ROOM, showComposer: false, ...place },
      global: { plugins: [vuetify, i18n] },
    })
    await settle()
    const row = container.querySelector<HTMLElement>('[data-mid="h1"]')!
    expect(row).not.toBeNull()

    const expand = await within(row).findByRole('button', { name: '展开' })
    await waitFor(() => expect(row.querySelector('.fold-clamped')).not.toBeNull())

    await fireEvent.click(expand)
    await waitFor(() => expect(row.querySelector('.fold-clamped')).toBeNull())
    const collapse = within(row).getByRole('button', { name: '收起' })

    await fireEvent.click(collapse)
    await waitFor(() => expect(row.querySelector('.fold-clamped')).not.toBeNull())
  })

  it('频道主线上照旧整条摊开', async () => {
    history = [humanMsg('h1', LONG)]
    const { container } = render(Panel, {
      props: { topic: ROOM, showComposer: false },
      global: { plugins: [vuetify, i18n] },
    })
    await settle()
    const row = container.querySelector<HTMLElement>('[data-mid="h1"]')!
    expect(row).not.toBeNull()
    expect(row.textContent).toContain('报错第 80 行')
    expect(within(row).queryByRole('button', { name: '展开' })).toBeNull()
    expect(row.querySelector('.fold-clamped')).toBeNull()
  })
})
