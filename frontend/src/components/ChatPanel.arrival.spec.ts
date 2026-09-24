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
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listProjectLibrary: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listTopicMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...args: unknown[]) => listBlocks(...args),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    chatWsUrl: () => 'ws://test/chat',
  }
})

import ChatPanel from './ChatPanel.vue'

const topic: Topic = {
  id: 'arrival-topic',
  project_id: 'p1',
  parent_id: null,
  title: 'Arrival',
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

function block(id: string, author: string, content: string): Block {
  return {
    id,
    topic_id: topic.id,
    kind: 'message',
    author_type: 'participant',
    author,
    content,
    created_at: '2026-08-17T00:00:01Z',
  } as Block
}

async function flush() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

function mount() {
  const vuetify = createVuetify({ components, directives })
  return render(ChatPanel, {
    props: { topic, topicList: [topic] },
    global: { plugins: [vuetify] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

// 新消息进来时淡入一下，说的是「刚来的是这条」。打开房间时读出来的历史不演：一屏
// 几十条同时浮上来，什么也说明不了。
describe('新消息进来', () => {
  it('打开房间时已经在的消息不演', async () => {
    listBlocks.mockResolvedValue({ data: [block('old', 'bob', '早上好')], has_more: false })
    const view = mount()
    await flush()
    expect(view.getByText('早上好').closest('.tl-arrive')).toBeNull()
  })

  it('之后实时进来的那一条演一次', async () => {
    listBlocks.mockResolvedValue({ data: [block('old', 'bob', '早上好')], has_more: false })
    const view = mount()
    await flush()

    FakeWebSocket.instances.at(-1)!.emit({ type: 'assistant_block', block: block('new', 'cheese-session', '收到') })
    await flush()

    expect(view.getByText('收到').closest('.tl-arrive')).not.toBeNull()
    expect(view.getByText('早上好').closest('.tl-arrive')).toBeNull()
  })
})
