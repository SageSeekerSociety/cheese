// 哪一条是我说的。
//
// 判据必须是 handle 而不是 author_type —— 这一列里有好几个人，按「是人还是 AI」
// 分，别人说的话会跟我的归成一类。
//
// 这件事曾经由左右分栏来表达（2026-09-09, <@符露夀> 定：我靠右、别人和芝士靠左），
// 现在由名字那一行的轻重表达（自己的压到 --muted）—— 气泡去掉之后，长内容不再
// 适合被推到半边去。**换掉的是表现，不是语义**：类名还是它，问的还是同一个问题，
// 所以这一份原样成立。
//
// 像素不在这里断言：happy-dom 不排版，量出来的只会是我们自己写进去的那串字符串。
// 这一份只问「哪一条挂了 --self」。
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

function msg(id: string, author: string, content = id): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'message',
    author_type: 'participant',
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
  localStorage.setItem('user', JSON.stringify({ id: 1, username: ME, nickname: '我' }))
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
  // Array.from 而不是展开：NodeList 的迭代器不在这套 tsconfig 的 lib 里，展开会
  // 报 TS2488（仓库里其它 spec 也一律用 Array.from）。
  return Array.from(container.querySelectorAll('.im-row')).map((r) => r.classList.contains('im-row--self'))
}

describe('哪一条是我说的', () => {
  it('我说的认得出来，别人和芝士都不是', async () => {
    history = [msg('a', 'bobby', '别人说的'), msg('b', ME, '我说的'), msg('c', SEAT, '芝士说的')]
    expect(sides(await open())).toEqual([false, true, false])
  })

  it('按 handle 判，不按「是人还是 AI」', async () => {
    // 这一条是这份用例存在的理由：房间里是「多个人 + 一个芝士」，按 author_type
    // 分的话，别人说的话会和我的归成一类，这个标记就不再是「我」了。
    history = [msg('a', 'bobby', '别人也是人'), msg('b', ME, '我')]
    const rows = Array.from((await open()).querySelectorAll('.im-row'))
    expect(rows[0].classList.contains('im-row--self'), 'bobby 和我同是参与者，但不是我').toBe(false)
    expect(rows[1].classList.contains('im-row--self')).toBe(true)
  })

  it('我连着说的第二条也还算我说的', async () => {
    // 续行不带头像和名字，所以它自己说不出是谁说的 —— 这个标记掉了，它就归到别人名下。
    history = [msg('a', ME, '第一句'), msg('b', ME, '第二句')]
    const rows = Array.from((await open()).querySelectorAll('.im-row'))
    expect(rows[1].classList.contains('im-row--cont'), '第二条是续行').toBe(true)
    expect(rows[1].classList.contains('im-row--self')).toBe(true)
  })
})
