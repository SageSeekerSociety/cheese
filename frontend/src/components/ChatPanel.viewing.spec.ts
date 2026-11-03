// 「芝士会看到你在看什么」接在发送那一层：右栏开着一份文件时，发出去的消息带着
// 一条 `viewing` 引用；读者点掉那一行，就不带。挂在对话栏这一层验，因为出事的是撰写框
// 到发件箱这一段接线——只测那颗按钮，接线断了它照样绿。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { computed } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'
import { createQuestionSubmit } from '@/lib/previewQuestion'
import { useShareViewing, VIEWING_CONTEXT } from '@/lib/viewingContext'
import { fillComposer } from '@/test/composerInput'
import { loadMarkdownReader } from '@/test/markdownReader'

const Panel = ChatPanel as unknown as Component
let vuetify: ReturnType<typeof createVuetify>
const sent: Record<string, unknown>[] = []
const history: Block[] = [
  {
    id: 'm1',
    project_id: 'p1',
    conversation_id: 't1',
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
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  sent.length = 0
  useShareViewing().share.value = true
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
      send() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => ({
    ok: true,
    status: 200,
    json: async () =>
      init?.method === 'POST' && String(url).endsWith('/messages')
        ? { code: 200, data: posted(JSON.parse(String(init.body))) }
        : String(url).includes('/progress')
          ? { code: 200, data: { items: [], updated_at: null } }
          : String(url).includes('/tasks')
            ? { code: 200, data: { data: [], total: 0 } }
            : { code: 200, data: { data: history, total: history.length, has_more: false } },
  }))
})

// 发出去的那条：记下请求体，回一条落了库的块。
function posted(body: Record<string, unknown>): Block {
  sent.push(body)
  return {
    id: String(body.request_id),
    conversation_id: 't1',
    kind: 'message',
    author: 'me',
    content: String(body.content),
    reply_to: (body.reply_to as string | undefined) ?? null,
    meta: { client_id: body.request_id },
    created_at: new Date().toISOString(),
  } as unknown as Block
}

const settle = () => new Promise((r) => setTimeout(r, 0))

async function send(text: string, toggleOff: boolean) {
  const view = render(Panel, {
    props: { topic: { id: 't1', project_id: 'p1', title: 't1', kind: 'topic' } as Topic, showComposer: true },
    global: {
      plugins: [vuetify, i18n],
      provide: {
        [VIEWING_CONTEXT as symbol]: computed(() => ({
          kind: 'viewing' as const,
          path: 'output/招募方案.docx',
          task_id: null,
        })),
      },
    },
  })
  await settle()
  if (toggleOff) await fireEvent.click(view.getByTestId('composer-viewing'))
  const box = view.container.querySelector<HTMLElement>('.ProseMirror') as HTMLElement
  box.focus()
  await fillComposer(box, text)
  await fireEvent.keyDown(box, { key: 'Enter' })
  await settle()
  view.unmount()
  return sent.find((f) => f.content === text)
}

beforeAll(loadMarkdownReader)

describe('发消息时带上右栏开着的那一份', () => {
  it('右栏开着一份文件：发出去的消息带着它', async () => {
    const frame = await send('这一页太挤了', false)
    expect(frame?.quoted_context).toEqual({ kind: 'viewing', path: 'output/招募方案.docx', task_id: null })
  })

  it('读者点掉那一行：发出去的不带', async () => {
    const frame = await send('这句话别带上', true)
    expect(frame).toBeTruthy()
    expect(frame?.quoted_context ?? null).toBeNull()
  })

  it('任务对话中点「发给任务芝士」按任务自己的 conversationId 走结构化引用，且被动 viewing 引用不在时间线画引用框', async () => {
    const view = render(Panel, {
      props: {
        topic: { id: 'room-1', project_id: 'p1', title: 'room', kind: 'topic' } as Topic,
        conversationId: 'task-99',
        agentHandle: 'cheese-bot',
        members: [{ user_handle: 'cheese-bot', nickname: '芝士', role: 'agent' } as never],
        showComposer: true,
      },
      global: {
        plugins: [vuetify, i18n],
        provide: {
          [VIEWING_CONTEXT as symbol]: computed(() => ({
            kind: 'viewing' as const,
            path: 'output/招募方案.docx',
            task_id: 'task-99',
          })),
        },
      },
    })
    await settle()
    const box = view.container.querySelector<HTMLElement>('.ProseMirror') as HTMLElement
    box.focus()
    await fillComposer(box, '普通消息不画引用框')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await settle()
    expect(view.container.querySelector('.message-quote')).toBeNull()

    view.unmount()

    const dispatched: unknown[] = []
    const submit = createQuestionSubmit({
      topic: () => ({ id: 'room-1', project_id: 'p1', title: 'room', kind: 'topic' }) as Topic,
      conversationId: () => 'task-99',
      agentSeat: () => ({ handle: 'cheese-a1b2c3d4e5f6', label: '芝士' }),
      mentionPool: () => [],
      topicList: () => [],
      send: (content, summon, atts, quoted) => {
        dispatched.push({ content, summon, atts, quoted })
        return true
      },
    })
    const ok = submit({
      intent: 'ask-agent',
      topicId: 'task-99',
      content: '把这里改短',
      quotedContext: {
        kind: 'text-range',
        path: 'docs/a.md',
        source: 'live',
        version: 'v1',
        task_id: 'task-99',
        text: '原文',
        heading: null,
        prefix: '前',
        suffix: '后',
      },
    })
    expect(ok).toBe(true)
    expect(dispatched).toHaveLength(1)
  })
})
