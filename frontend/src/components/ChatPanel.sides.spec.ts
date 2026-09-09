// 一条消息站在哪一边。
//
// 分栏 (2026-09-09, <@符露夀> 定): 我说的话靠右，别人和芝士靠左。侧只回答
// 「这条是不是我说的」，所以判据必须是 handle 而不是 author_type —— 这一列里
// 有好几个人，按「是人还是 AI」分，别人说的话会跟着我跑到右边去。
//
// 像素（气泡多宽、留白多少）不在这里断言：happy-dom 不排版，量出来的只会是我们
// 自己写进去的那串字符串。这一份只问「哪一条挂了 --self」，那是分栏的全部语义。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

const Panel = ChatPanel as unknown as Component
const ME = 'me'
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

function msg(id: string, author: string, author_type: 'human' | 'ai', content = id): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'message',
    author_type,
    author,
    content,
    reply_to: null,
    refs: [],
    created_at: new Date().toISOString(),
  } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  history = []
  localStorage.setItem('cheesex.me', JSON.stringify({ id: '1', handle: ME, name: '我', token: '' }))
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
              { id: '1', member_handle: ME, name: '我', role: 'owner', agent: false },
              { id: '2', member_handle: SEAT, name: '芝士', role: 'member', agent: true },
              { id: '3', member_handle: 'bobby', name: 'Bob', role: 'member', agent: false },
            ],
            total: 3,
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

async function open() {
  const { container } = render(Panel, {
    props: { topic: topicOf('t1'), showComposer: false },
    global: { plugins: [vuetify] },
  })
  await settle()
  return container
}

/** 每一条消息行是不是「我说的」，按时间线顺序。 */
function sides(container: Element): boolean[] {
  return [...container.querySelectorAll('.im-row')].map((r) => r.classList.contains('im-row--self'))
}

describe('消息站在哪一边', () => {
  it('我说的靠右，别人和芝士靠左', async () => {
    history = [
      msg('a', 'bobby', 'human', '别人说的'),
      msg('b', ME, 'human', '我说的'),
      msg('c', SEAT, 'ai', '芝士说的'),
    ]
    expect(sides(await open())).toEqual([false, true, false])
  })

  it('按 handle 判，不按「是人还是 AI」', async () => {
    // 这一条是这份用例存在的理由：房间里是「多个人 + 一个芝士」，按 author_type
    // 分的话，别人说的话会和我的一起跑到右边去，右边就不再是「我」了。
    history = [msg('a', 'bobby', 'human', '别人也是人'), msg('b', ME, 'human', '我')]
    const rows = [...(await open()).querySelectorAll('.im-row')]
    expect(rows[0].classList.contains('im-row--self'), 'bobby 也是 human，但不是我').toBe(false)
    expect(rows[1].classList.contains('im-row--self')).toBe(true)
  })

  it('我连着说的第二条仍然在右边', async () => {
    // 续行不带头像和名字，「是谁说的」这时全靠位置 —— 掉到左边就成了别人的话。
    history = [msg('a', ME, 'human', '第一句'), msg('b', ME, 'human', '第二句')]
    const rows = [...(await open()).querySelectorAll('.im-row')]
    expect(rows[1].classList.contains('im-row--cont'), '第二条是续行').toBe(true)
    expect(rows[1].classList.contains('im-row--self')).toBe(true)
  })
})
