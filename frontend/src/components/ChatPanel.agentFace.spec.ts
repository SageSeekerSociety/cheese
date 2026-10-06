import type { Block, Topic, WsServerFrame } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()
const listTopicMembers = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listProjectLibrary: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listTopicMembers: (...args: unknown[]) => listTopicMembers(...args),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...args: unknown[]) => listBlocks(...args),
    chatWsUrl: () => 'ws://test/chat',
  }
})

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

const topic: Topic = {
  id: 'agent-face-topic',
  project_id: 'p1',
  parent_id: null,
  title: 'Agent face',
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

function block(id: string, author: string, content: string, second: number, extra: Partial<Block> = {}): Block {
  return {
    id,
    conversation_id: topic.id,
    kind: 'message',
    author_type: author === 'system' ? 'platform' : 'participant',
    author,
    content,
    created_at: `2026-08-17T00:00:${String(second).padStart(2, '0')}Z`,
    ...extra,
  } as Block
}

async function flush() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

// 在动的头像读出来是「名字：它此刻在干什么，点击查看现场」。
const LIVE = /点击查看现场/

function rowOf(el: HTMLElement): string | undefined {
  return (el.closest('[data-mid]') as HTMLElement | null)?.dataset.mid
}

async function mount(history: Block[]) {
  listBlocks.mockResolvedValue({ data: history, has_more: false })
  const vuetify = createVuetify({ components, directives })
  const view = render(ChatPanel, {
    props: { topic, topicList: [topic] },
    global: { plugins: [vuetify, i18n] },
  })
  await flush()
  return { view, socket: FakeWebSocket.instances.at(-1)! }
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  FakeWebSocket.instances = []
  listTopicMembers.mockResolvedValue({ data: [], total: 0 })
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

// 队友在干活时，对话里它最近出现的那个头像跟着状态动，更早的头像不动；这一轮结束，
// 头像不再指向现场。
describe('对话里在动的头像', () => {
  const earlier = block('a-1', 'cheese-a1', '表格模板放好了。', 1)
  const ask = block('h-1', 'lin', '@芝士 帮我整理第三组数据', 2)
  const latest = block('a-2', 'cheese-a1', '好，我先看一下格式。', 3)

  it('只有这位队友最新的那个头像在动', async () => {
    const { view, socket } = await mount([earlier, ask, latest])
    socket.emit({ type: 'turn_started', turn_id: 't1', agent: 'cheese-a1' })
    await flush()

    const live = view.queryAllByRole('button', { name: LIVE })
    expect(live.map(rowOf)).toEqual(['a-2'])
  })

  it('还没开口：动的是它上一次说话的那个头像', async () => {
    const { view, socket } = await mount([earlier, ask])
    socket.emit({ type: 'turn_started', turn_id: 't1', agent: 'cheese-a1' })
    await flush()

    expect(view.queryAllByRole('button', { name: LIVE }).map(rowOf)).toEqual(['a-1'])
  })

  it('悬停说的是现场顶上那一句', async () => {
    const { view, socket } = await mount([earlier, ask, latest])
    socket.emit({ type: 'turn_started', turn_id: 't1', agent: 'cheese-a1' })
    socket.emit({
      type: 'event_block',
      block: {
        ...block('e-1', 'system', '', 4, { kind: 'event', turn_id: 't1' }),
        meta: { event_type: 'api_retry', attempt: 2 },
      } as Block,
    })
    await flush()

    const [live] = view.queryAllByRole('button', { name: LIVE })
    await fireEvent.mouseEnter(live)
    await waitFor(() => expect(screen.getByText(/重试中（第 2 次）/)).toBeTruthy())
  })

  it('两位队友同时在干：各自最新的那个头像都在动', async () => {
    const other = block('b-1', 'cheese-b2', '我来看第二节。', 4)
    const { view, socket } = await mount([earlier, ask, latest, other])
    socket.emit({ type: 'turn_started', turn_id: 't1', agent: 'cheese-a1' })
    socket.emit({ type: 'turn_started', turn_id: 't2', agent: 'cheese-b2' })
    await flush()

    expect(view.queryAllByRole('button', { name: LIVE }).map(rowOf).sort()).toEqual(['a-2', 'b-1'])
  })

  it('点在动的头像去现场', async () => {
    const { view, socket } = await mount([earlier, ask, latest])
    socket.emit({ type: 'turn_started', turn_id: 't1', agent: 'cheese-a1' })
    await flush()

    view.getByRole('button', { name: LIVE }).click()
    expect((view.emitted('open-resource') as unknown[][] | undefined)?.at(-1)?.[0]).toBe('site')
    expect(view.emitted('mention-click')).toBeUndefined()
  })

  it('这一轮结束：头像不再指向现场', async () => {
    const { view, socket } = await mount([earlier, ask, latest])
    socket.emit({ type: 'turn_started', turn_id: 't1', agent: 'cheese-a1' })
    await flush()
    socket.emit({ type: 'turn_finished', turn_id: 't1' })
    await flush()

    expect(view.queryAllByRole('button', { name: LIVE })).toEqual([])
  })

  it('读屏读到的名字不随秒数变，秒数只在悬停的那一句里走', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    try {
      const { view, socket } = await mount([earlier, ask, latest])
      socket.emit({ type: 'turn_started', turn_id: 't1', agent: 'cheese-a1' })
      await flush()
      const live = view.getByRole('button', { name: LIVE })
      const name = live.getAttribute('aria-label') ?? ''

      vi.advanceTimersByTime(3000)
      await flush()

      // 名字一变，读屏就可能再念一遍：每秒念一次「已用 N 秒」。
      expect(view.getByRole('button', { name })).toBe(live)
      await fireEvent.mouseEnter(live)
      await waitFor(() => expect(screen.getByText(/已用 3 秒/)).toBeTruthy())
    } finally {
      vi.useRealTimers()
    }
  })
})
