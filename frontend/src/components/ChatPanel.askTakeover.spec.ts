// 提问接管输入框。
//
// 队友问一组问题时，那一组直接占据输入框那格：不用点「打开」，问题也不再以卡片
// 的形式内嵌在对话流里。两者互斥——同一个位置要么是提问面板，要么是 composer。
// Esc 只是把它收起来，问题不会消失：收起后「有 N 个问题待回答」那一条能叫回来。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

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

function askBlock(id: string, index: number, members: string[], answered = false): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'message',
    author_type: 'participant',
    author: 'agent',
    content: `问题 ${index + 1}`,
    reply_to: null,
    refs: [],
    created_at: '2026-08-01T00:00:00Z',
    meta: {
      asked: 'me',
      options: [{ text: 'A' }, { text: 'B' }],
      allow_other: true,
      reject_option: true,
      answer_log: answered
        ? [{ v: 1, kind: 'option', option: 'A', note: '', by: 'me', at: null, client_op_id: `op-${id}` }]
        : [],
      ask_group: { id: 'group-1', asked_by: 'agent', members, index, total: members.length },
    },
  } as unknown as Block
}

function groupData(members: string[], answered = false) {
  const group = { topic_id: 't1', asked_by: 'agent', id: 'group-1', members, total: members.length }
  return {
    group,
    settlement: null,
    receipt: null,
    blocks: members.map((id, index) => askBlock(id, index, members, answered)),
  }
}

let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []
let group: ReturnType<typeof groupData> | null = null

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  history = []
  group = null
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
    json: async () =>
      String(url).includes('/topics/asks/')
        ? { code: 200, data: group }
        : String(url).includes('/progress')
          ? { code: 200, data: { items: [], updated_at: null } }
          : String(url).includes('/tasks')
            ? { code: 200, data: { data: [], total: 0 } }
            : { code: 200, data: { data: history, total: history.length, has_more: false } },
  }))
})

const settle = () => new Promise((r) => setTimeout(r, 0))
async function flush(times = 6) {
  for (let i = 0; i < times; i++) await settle()
}

async function open() {
  const ui = render(Panel, {
    props: { topic: topicOf('t1'), showComposer: true },
    global: { plugins: [vuetify, i18n] },
  })
  await flush()
  return ui
}

describe('提问接管输入框', () => {
  it('把 composer 换成提问面板，而不是把它叠在上面', async () => {
    const members = ['b1', 'b2']
    history = members.map((id, index) => askBlock(id, index, members))
    group = groupData(members)
    const { container } = await open()
    expect(container.querySelector('.ask-group--composer')).toBeTruthy()
    expect(container.querySelector('.composer')).toBeNull()
  })

  it('整组都答完之后 composer 自己回来', async () => {
    const members = ['b1', 'b2']
    history = members.map((id, index) => askBlock(id, index, members, true))
    group = groupData(members, true)
    const { container } = await open()
    expect(container.querySelector('.ask-group--composer')).toBeNull()
    expect(container.querySelector('.composer')).toBeTruthy()
  })

  it('没有问题时就和平常一样只有 composer', async () => {
    const { container } = await open()
    expect(container.querySelector('.composer')).toBeTruthy()
    expect(container.querySelector('.ask-group--composer')).toBeNull()
  })

  it('Esc 收起后问题还在：那一条能把面板叫回来', async () => {
    const members = ['b1', 'b2']
    history = members.map((id, index) => askBlock(id, index, members))
    group = groupData(members)
    const { container } = await open()
    const card = container.querySelector('.ask-group--composer')!
    await fireEvent.keyDown(card, { key: 'Escape' })
    await flush()
    expect(container.querySelector('.ask-group--composer')).toBeNull()
    expect(container.querySelector('.composer')).toBeTruthy()
    const pill = container.querySelector('.composer-ask-return') as HTMLElement | null
    expect(pill?.textContent?.trim()).toBe('有 2 个问题待回答')
    await fireEvent.click(pill!)
    await flush()
    expect(container.querySelector('.ask-group--composer')).toBeTruthy()
  })
})
