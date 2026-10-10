// 芝士问的题是对话里的一条普通消息，下面带快捷回复。
//
// 点一个快捷回复，就是把那几个字作为对这道题的回复发出去 —— 和在输入框里打字是
// 同一条消息；输入框始终在，从不被题占用。
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

function question(topic: string, answered = false): Block {
  return {
    id: `q-${topic}`,
    project_id: 'p1',
    topic_id: topic,
    kind: 'message',
    author_type: 'participant',
    author: 'agent',
    content: '预算按哪个口径统计？',
    reply_to: null,
    refs: [],
    created_at: '2026-08-01T00:00:00Z',
    meta: {
      asked: 'me',
      options: [{ text: '按部门', explain: '和去年的报表对得上' }, { text: '按项目' }],
      answer_log: answered
        ? [{ kind: 'option', option: '按部门', note: null, by: 'me', at: '2026-08-01T00:01:00Z', reply_id: 'r1' }]
        : [],
    },
  } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []
let sent: Array<{ url: string; body: Record<string, unknown> }> = []

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  history = []
  sent = []
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
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    if (String(url).includes('/messages') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      sent.push({ url: String(url), body })
      return {
        ok: true,
        status: 200,
        json: async () => ({
          code: 200,
          data: { ...question('sent'), id: 'reply', content: body.content, reply_to: body.reply_to, meta: {} },
        }),
      }
    }
    return {
      ok: true,
      status: 200,
      json: async () =>
        String(url).includes('/progress')
          ? { code: 200, data: { items: [], updated_at: null } }
          : String(url).includes('/tasks')
            ? { code: 200, data: { data: [], total: 0 } }
            : { code: 200, data: { data: history, total: history.length, has_more: false } },
    }
  })
})

const settle = () => new Promise((r) => setTimeout(r, 0))
async function until(ready: () => boolean, times = 200) {
  for (let i = 0; i < times && !ready(); i++) await settle()
}

async function open(topic: string, extra: Record<string, unknown> = {}) {
  const ui = render(Panel, {
    props: { topic: topicOf(topic), showComposer: true, ...extra },
    global: { plugins: [vuetify, i18n] },
  })
  await until(() => !!ui.container.querySelector('[data-mid]'))
  return ui
}

describe('芝士的提问是一条带快捷回复的消息', () => {
  it('题画在对话里，输入框照样在', async () => {
    // 每一格一间房：时间线按房间缓存在模块里，同一间会串味。
    history = [question('t-open')]
    const { container, getByRole } = await open('t-open')
    expect(container.textContent).toContain('预算按哪个口径统计？')
    expect(getByRole('button', { name: /按部门/ })).toBeTruthy()
    expect(container.querySelector('.composer')).toBeTruthy()
  })

  it('点一个快捷回复，就是把它作为对这道题的回复发出去', async () => {
    history = [question('t-click')]
    const { getByRole } = await open('t-click')
    await fireEvent.click(getByRole('button', { name: /按项目/ }))
    await until(() => sent.length > 0)
    expect(sent).toHaveLength(1)
    expect(sent[0].url).toContain('/topics/t-click/messages')
    expect(sent[0].body.content).toBe('按项目')
    expect(sent[0].body.reply_to).toBe('q-t-click')
  })

  it('任务关了、没人答过的题不再给点，说它不再等回答', async () => {
    history = [question('t-closed')]
    const { container, queryByRole } = await open('t-closed', { askClosed: true })
    expect(container.textContent).toContain('预算按哪个口径统计？')
    expect(queryByRole('button', { name: /按部门/ })).toBeNull()
    expect(container.querySelector('.ask-replies')?.textContent).toContain('已结束，不再等回答')
  })

  it('有人答过之后，快捷回复收成谁说了什么', async () => {
    history = [question('t-done', true)]
    const { container, queryByRole } = await open('t-done')
    expect(queryByRole('button', { name: /按项目/ })).toBeNull()
    expect(container.querySelector('.ask-replies__answers')?.textContent).toContain('按部门')
    expect(container.querySelector('.composer')).toBeTruthy()
  })
})
