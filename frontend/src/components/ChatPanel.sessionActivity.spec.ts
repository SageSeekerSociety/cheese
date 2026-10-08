import type { Block, Topic, WsServerFrame } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()
const listTopicMembers = vi.fn()

vi.mock('@/lib/roomLink', () => import('@/test/fakeRoomLink'))
vi.mock('../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../lib/libraryApi')>('../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listTopicMembers: (...args: unknown[]) => listTopicMembers(...args),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...args: unknown[]) => listBlocks(...args),
  }
})

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

const topic: Topic = {
  id: 'session-activity-topic',
  project_id: 'p1',
  parent_id: null,
  title: 'Session activity',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-17T00:00:00Z',
  updated_at: '2026-08-17T00:00:00Z',
} as Topic

class FakeWebSocket {
  static OPEN = 1
  static instances: FakeWebSocket[] = []

  readyState = FakeWebSocket.OPEN
  onmessage: ((event: MessageEvent) => void) | null = null
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor() {
    FakeWebSocket.instances.push(this)
  }

  close() {}
  send() {}

  emit(frame: WsServerFrame) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent)
  }
}

const assistantBlock: Block = {
  id: 'assistant-1',
  conversation_id: topic.id,
  kind: 'message',
  author_type: 'participant',
  author: 'cheese-session',
  content: 'Still working.',
  created_at: '2026-08-17T00:00:01Z',
} as Block

// 队友的步骤清单是它发在房间里的一条消息（`todo_write`），和它别的话一样。
const checklist: Block = {
  ...assistantBlock,
  id: 'checklist-1',
  content: '✓ Read the brief\n✱ Write the fix',
  turn_id: 'one',
  meta: {
    checklist: {
      items: [
        { id: '1', subject: 'Read the brief', status: 'completed' },
        { id: '2', subject: 'Write the fix', status: 'in_progress' },
      ],
      result: null,
    },
  },
} as Block

async function flush() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  FakeWebSocket.instances = []
  listBlocks.mockResolvedValue({ data: [], has_more: false })
  listTopicMembers.mockResolvedValue({ data: [], total: 0 })
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

describe('session activity', () => {
  it('reports working until every active work id finishes', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'turn_started', turn_id: 'one' })
    socket.emit({ type: 'turn_started', turn_id: 'two' })
    socket.emit({ type: 'assistant_block', block: assistantBlock })
    socket.emit({ type: 'done' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([true])

    socket.emit({ type: 'turn_finished', turn_id: 'one' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([true])

    socket.emit({ type: 'turn_finished', turn_id: 'two' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([false])
  })

  // 队友在房间里和别人一样：干活时对话里不另起一行「正在处理」，也没有一行跟着
  // 这一轮来去的清单。它的清单是它发的一条消息，这一轮结束了还在原处。
  it('a running turn adds no working line and no checklist row to the chat', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'turn_started', turn_id: 'one' })
    socket.emit({ type: 'todo', items: [{ id: '1', subject: 'Transient step', status: 'in_progress' }] })
    await flush()
    expect(view.container.textContent).not.toMatch(/正在处理|正在交给/)
    expect(view.queryByRole('button', { name: /停止/ })).toBeNull()
    expect(view.queryByText('Transient step')).toBeNull()

    socket.emit({ type: 'assistant_block', block: checklist })
    await flush()
    expect(view.getByText('Write the fix')).toBeTruthy()

    socket.emit({
      type: 'block_updated',
      block: {
        ...checklist,
        content: '✓ Read the brief\n✓ Write the fix',
        meta: {
          edited_at: '2026-08-17T00:00:05Z',
          checklist: {
            items: [
              { id: '1', subject: 'Read the brief', status: 'completed' },
              { id: '2', subject: 'Write the fix', status: 'completed' },
            ],
            result: null,
          },
        },
      } as Block,
    })
    socket.emit({ type: 'turn_finished', turn_id: 'one' })
    await flush()
    expect(view.getAllByText('Write the fix')).toHaveLength(1)
    expect(view.getByText('已编辑')).toBeTruthy()
  })

  // 「现场」那一格靠这个事件在开工那一刻出现。以前它等的是第一个工具调用——而一个
  // @ 出来的 agent 可能先想上半分钟才动手，那半分钟里右边什么都没有，只有刷新
  // 一次页面才看得见它。干活的证据不是干活的开始。
  it('开工那一刻就报 working，不等第一个工具调用', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'turn_started', turn_id: 'one' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([true])

    socket.emit({ type: 'turn_finished', turn_id: 'one' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([false])
  })

  // 重连（掉线自动重连、切回这个话题）会重跑一次 loadTopic。上一轮早就结束了，
  // 而 working 是靠事件翻回去的——不报 false 的话，右边那格现场会在一个没人干活的
  // 话题上一直亮着。
  it('重新载入话题时把 working 报回 false', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    FakeWebSocket.instances.at(-1)!.emit({ type: 'turn_started', turn_id: 'one' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([true])

    await view.rerender({ topic: { ...topic, id: 'another-topic' } as Topic, topicList: [topic] })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([false])
  })
})
