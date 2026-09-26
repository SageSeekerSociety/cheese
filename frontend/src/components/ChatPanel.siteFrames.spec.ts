// 现场那一格自己没有 socket：它的每一行都是对话栏从房间 socket 上收到、转过去的。
// 漏转一种帧不报错，只是现场又回到「打开才刷新」。
import type { Block, Topic, WsServerFrame } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listProjectLibrary: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listTopicMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: vi.fn().mockResolvedValue({ data: [], has_more: false }),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    chatWsUrl: () => 'ws://test/chat',
  }
})

import ChatPanel from './ChatPanel.vue'

const topic = {
  id: 'site-frames-topic',
  project_id: 'p1',
  parent_id: null,
  title: 'Site frames',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
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

function event(id: string, extra: Partial<Block> = {}): Block {
  return {
    id,
    topic_id: topic.id,
    kind: 'event',
    author_type: 'participant',
    author: 'cheese-session',
    content: '',
    turn_id: 'turn-a',
    meta: { tool: 'Bash', arg: 'make test', in_room: false },
    created_at: '2026-09-25T10:00:00Z',
    ...extra,
  } as Block
}

async function flush() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

async function open() {
  const vuetify = createVuetify({ components, directives })
  const view = render(ChatPanel, { props: { topic, topicList: [topic] }, global: { plugins: [vuetify] } })
  await flush()
  return { view, socket: FakeWebSocket.instances.at(-1)! }
}

function siteBlocks(view: ReturnType<typeof render>): string[] {
  return view.emitted<[Block]>('site-block').map(([block]) => block.id)
}

beforeEach(() => {
  vi.clearAllMocks()
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

describe('对话栏把现场的帧转过去', () => {
  it('房间自己的事件行转过去；分身卡上的、消息，不转', async () => {
    const { view, socket } = await open()

    socket.emit({ type: 'event_block', block: event('step-1') })
    socket.emit({ type: 'event_block', block: event('card-step', { task_id: 'card-1' }) })
    socket.emit({ type: 'assistant_block', block: event('msg', { kind: 'message', content: '好了' }) })
    await flush()

    expect(siteBlocks(view)).toEqual(['step-1'])
  })

  it('一行原地变了：对话里那一行换成新的，现场也收到新的那一份', async () => {
    const { view, socket } = await open()
    const notice = event('retry-1', {
      author: 'system',
      author_type: 'platform',
      content: 'AI 服务请求失败，正在重试（第 1/10 次）',
      meta: { event_type: 'api_retry', severity: 'warn', who: 'platform', attempt: 1 },
    })
    socket.emit({ type: 'event_block', block: notice })
    await flush()
    expect(view.container.textContent).toContain('第 1/10 次')

    socket.emit({
      type: 'block_updated',
      block: { ...notice, content: 'AI 服务请求失败，正在重试（第 2/10 次）', meta: { ...notice.meta, attempt: 2 } },
    })
    await flush()

    expect(view.container.textContent).toContain('第 2/10 次')
    expect(view.container.textContent).not.toContain('第 1/10 次')
    const [last] = view.emitted<[Block]>('site-block').at(-1)!
    expect(last.meta?.attempt).toBe(2)
  })

  it('在跑的轮次和它们的开始时间：中途连上的按后端给的时间，收工的拿掉', async () => {
    const { view, socket } = await open()

    socket.emit({ type: 'turn_active', turn_ids: ['turn-a'], since: { 'turn-a': 1_790_000_000 } })
    await flush()
    expect(view.emitted<[Record<string, number>]>('site-turns').at(-1)![0]).toEqual({ 'turn-a': 1_790_000_000_000 })

    socket.emit({ type: 'turn_finished', turn_id: 'turn-a' })
    await flush()
    expect(view.emitted<[Record<string, number>]>('site-turns').at(-1)![0]).toEqual({})
  })
})
