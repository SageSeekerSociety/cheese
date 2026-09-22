// 芝士摆出来的一份东西（`cheese show`）在对话里长什么样。
//
// 后端从一开始就往房间时间线写这样一块（`POST /topics/{id}/shown` → kind=artifact，
// content 是工作区里的路径），而对话栏一直没有认它的分支：它掉进最下面那个兜底，
// 按 content 渲染，于是屏幕上是一行光秃秃的 `report.html` —— 和芝士随口说了个文件名
// 长得一模一样，点不动，也看不出那是它做完交出来的东西。
//
// 这一份盯的是两件事：那一块**不是**一段正文，以及点它能把这份东西打开。
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

function block(kind: string, content: string, extra: Partial<Block> = {}): Block {
  return {
    id: `b-${content}`,
    project_id: 'p1',
    topic_id: 't1',
    kind,
    author_type: 'participant',
    author: SEAT,
    content,
    reply_to: null,
    refs: [],
    created_at: new Date().toISOString(),
    ...extra,
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

async function open() {
  const rendered = render(Panel, {
    props: { topic: topicOf('t1'), showComposer: false },
    global: { plugins: [vuetify] },
  })
  await settle()
  return rendered
}

describe('芝士摆出来的东西', () => {
  it('是一块可以打开的东西，不是一段正文', async () => {
    history = [block('artifact', 'out/report.html', { mime_type: 'text/html' })]
    const { container } = await open()

    const card = container.querySelector('.im-artifact')
    expect(card, '产物应当有自己的一块，而不是掉进正文的兜底分支').not.toBeNull()
    expect(container.querySelector('.im-text'), '它不是一段话').toBeNull()
  })

  it('卡上写的是文件名和它是什么，不是工作区里那条路径', async () => {
    history = [block('artifact', 'build/out/结题报告.pdf', { mime_type: 'application/pdf' })]
    const { container } = await open()

    const card = container.querySelector('.im-artifact')
    expect(card?.textContent).toContain('结题报告.pdf')
    expect(card?.textContent).toContain('PDF')
    // 路径的前几段是它在工作区里的位置，读的人用不上，也打不开。
    expect(card?.textContent).not.toContain('build/out')
  })

  it('点它把这份东西交出去开，并带上它属于哪条活', async () => {
    history = [block('artifact', 'demo.html', { mime_type: 'text/html', task_id: 'task-7' })]
    const { container, emitted } = await open()

    await (container.querySelector('.im-artifact') as HTMLElement).click()
    await settle()

    expect(emitted()['open-file']?.[0]).toEqual(['demo.html', 'task-7'])
  })

  it('认不出后缀时说一句中性的话，而不是把后缀摆上去', async () => {
    history = [block('artifact', 'thing.weird', { mime_type: 'text/html' })]
    const { container } = await open()

    const card = container.querySelector('.im-artifact')
    expect(card?.textContent).toContain('thing.weird')
    expect(card?.textContent).toContain('文件')
    expect(card?.textContent).not.toContain('weird 文件')
  })
})
