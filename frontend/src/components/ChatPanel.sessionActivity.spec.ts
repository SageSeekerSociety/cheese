import type { Block, Topic, WsServerFrame } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listBlocks: (...args: unknown[]) => listBlocks(...args),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    chatWsUrl: () => 'ws://test/chat',
  }
})

import ChatPanel from './ChatPanel.vue'

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
  topic_id: topic.id,
  kind: 'message',
  author_type: 'ai',
  author: 'cheese-session',
  content: 'Still working.',
  created_at: '2026-08-17T00:00:01Z',
} as Block

async function flush() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

beforeEach(() => {
  vi.clearAllMocks()
  FakeWebSocket.instances = []
  listBlocks.mockResolvedValue({ data: [], has_more: false })
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

describe('session activity', () => {
  it('keeps the working indicator until every active work id finishes', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify] },
    })
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'turn_started', turn_id: 'one' })
    socket.emit({ type: 'turn_started', turn_id: 'two' })
    socket.emit({ type: 'assistant_block', block: assistantBlock })
    socket.emit({ type: 'done' })
    await flush()
    expect(view.getByText('芝士正在处理…')).toBeTruthy()

    socket.emit({ type: 'turn_finished', turn_id: 'one' })
    await flush()
    expect(view.getByText('芝士正在处理…')).toBeTruthy()

    socket.emit({ type: 'turn_finished', turn_id: 'two' })
    await flush()
    expect(view.queryByText('芝士正在处理…')).toBeNull()
  })

  // 「现场」那一格靠这个事件在开工那一刻出现。以前它等的是第一个工具帧——而一个
  // @ 出来的 agent 可能先想上半分钟才动手，那半分钟里右边什么都没有，只有刷新
  // 一次页面才看得见它。工具帧是干活的证据，不是干活的开始。
  it('开工那一刻就报 working，不等第一个工具帧', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify] },
    })
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'turn_started', turn_id: 'one' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([true])
    expect(view.emitted('tool-used')).toBeUndefined()

    socket.emit({ type: 'turn_finished', turn_id: 'one' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([false])
  })

  // 重连（掉线自动重连、切回这个话题）会重跑一次 loadTopic。上一轮早就结束了，
  // 而房间里那句「正在处理…」是靠事件翻回去的——不报 false 的话，右边那格现场
  // 会在一个没人干活的话题上一直亮着。
  it('重新载入话题时把 working 报回 false', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify] },
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
