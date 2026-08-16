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
  id: 'turn-lifecycle-topic',
  project_id: 'p1',
  parent_id: null,
  title: 'Turn lifecycle',
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
  author: 'cheese-turn',
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

describe('explicit turn lifecycle', () => {
  it('keeps the working indicator until every active turn finishes', async () => {
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
    expect(view.getByText('芝士 正在看…')).toBeTruthy()

    socket.emit({ type: 'turn_finished', turn_id: 'one' })
    await flush()
    expect(view.getByText('芝士 正在看…')).toBeTruthy()

    socket.emit({ type: 'turn_finished', turn_id: 'two' })
    await flush()
    expect(view.queryByText('芝士 正在看…')).toBeNull()
  })
})
