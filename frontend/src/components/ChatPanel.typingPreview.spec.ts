// 队友在写给房间的那条消息：正文随模型生成一点点长出来，真的那条落下就换成它，
// 写不成就消失。它不是消息——任何一条出路漏掉，房间里就留下一条没人发过的话。
import type { Block, Topic, WsServerFrame } from '@/cx_types'
import type { LiveBlock } from '@/types/live'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/roomLink', () => import('@/test/fakeRoomLink'))
vi.mock('../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../lib/libraryApi')>('../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listTopicMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: vi.fn().mockResolvedValue({ data: [], has_more: false }),
  }
})

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

const topic = {
  id: 'typing-topic',
  project_id: 'p1',
  parent_id: null,
  title: 'Typing',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
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

const TYPING = '正在输入…'

async function flush() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

async function open() {
  const vuetify = createVuetify({ components, directives })
  const view = render(ChatPanel, { props: { topic, topicList: [topic] }, global: { plugins: [vuetify, i18n] } })
  await flush()
  return { view, socket: FakeWebSocket.instances.at(-1)! }
}

/** A chat_send call as Claude Code names it, its arguments streamed as far as `args`. */
function sending(args: string, name = 'mcp__native__chat_send'): LiveBlock {
  return { type: 'tool', id: 'call-1', name, arguments: args }
}

function live(blocks: LiveBlock[], agent = 'cheese', turn = 'turn-a'): WsServerFrame {
  return { type: 'live', turn_id: turn, agent, blocks }
}

function message(content: string, author = 'cheese'): Block {
  return {
    id: `msg-${author}`,
    conversation_id: topic.id,
    kind: 'message',
    author_type: 'participant',
    author,
    content,
    turn_id: 'turn-a',
    created_at: '2026-10-01T10:00:00Z',
  }
}

function text(view: ReturnType<typeof render>): string {
  return view.container.textContent ?? ''
}

function count(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1
}

// A message body is drawn by the Markdown reader, which MarkdownView imports the
// first time it draws. Loaded here, that import resolves at once; left to the
// first test, it can outlast `flush()` on a busy machine and the body is blank.
beforeAll(async () => {
  await import('@/components/panels/doc/blocks/reader')
})

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  vi.clearAllMocks()
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('队友正在写的那条消息', () => {
  it('正文随着一帧帧长出来，标着正在输入', async () => {
    const { view, socket } = await open()

    socket.emit(live([sending('{"content":"我先看')]))
    await flush()
    expect(text(view)).toContain('我先看')
    expect(text(view)).toContain(TYPING)

    socket.emit(live([sending('{"content":"我先看一下日志\\n马上')]))
    await flush()
    expect(text(view)).toContain('我先看一下日志')
    expect(text(view)).toContain('马上')
    expect(count(text(view), '我先看')).toBe(1)
  })

  it('pi 那边不带前缀的工具名同样认得', async () => {
    const { view, socket } = await open()

    socket.emit(live([sending('{"content":"好的', 'chat_send')]))
    await flush()

    expect(text(view)).toContain('好的')
  })

  it('真的那条落下：换成它，不出现两遍', async () => {
    const { view, socket } = await open()

    socket.emit(live([sending('{"content":"修好了，测试全过"}')]))
    socket.emit(live([]))
    socket.emit({ type: 'assistant_block', block: message('修好了，测试全过') })
    await flush()

    expect(count(text(view), '修好了，测试全过')).toBe(1)
    expect(text(view)).not.toContain(TYPING)
  })

  it('消息先于清空的那一帧到：也只剩真的那一条', async () => {
    const { view, socket } = await open()

    socket.emit(live([sending('{"content":"收到"}')]))
    socket.emit({ type: 'assistant_block', block: message('收到') })
    socket.emit(live([]))
    await flush()

    expect(count(text(view), '收到')).toBe(1)
    expect(text(view)).not.toContain(TYPING)
  })

  it('没写完就从帧里消失：立刻撤掉', async () => {
    const { view, socket } = await open()

    socket.emit(live([sending('{"content":"我觉得应该')]))
    await flush()
    expect(text(view)).toContain('我觉得应该')

    socket.emit(live([{ type: 'text', text: '换个思路' }]))
    await flush()

    expect(text(view)).not.toContain('我觉得应该')
    expect(text(view)).not.toContain(TYPING)
  })

  it('写完了却一直没有落下（被拦下、调用失败）：过一会儿撤掉', async () => {
    const { view, socket } = await open()
    vi.useFakeTimers()

    socket.emit(live([sending('{"content":"这条发不出去"}')]))
    socket.emit(live([]))
    await vi.advanceTimersByTimeAsync(100)
    // 消息通常在这之后很快落下，所以先留着。
    expect(text(view)).toContain('这条发不出去')

    await vi.advanceTimersByTimeAsync(10_000)

    expect(text(view)).not.toContain('这条发不出去')
  })

  it('那一轮结束：撤掉', async () => {
    const { view, socket } = await open()

    socket.emit({ type: 'turn_started', turn_id: 'turn-a', agent: 'cheese' })
    socket.emit(live([sending('{"content":"还在写')]))
    await flush()
    expect(text(view)).toContain('还在写')

    socket.emit({ type: 'turn_finished', turn_id: 'turn-a', agent: 'cheese' })
    await flush()

    expect(text(view)).not.toContain('还在写')
  })

  it('一直没有新帧：不会永远挂着', async () => {
    const { view, socket } = await open()
    vi.useFakeTimers()

    socket.emit(live([sending('{"content":"写到一半')]))
    await vi.advanceTimersByTimeAsync(100)
    expect(text(view)).toContain('写到一半')

    await vi.advanceTimersByTimeAsync(5 * 60_000)

    expect(text(view)).not.toContain('写到一半')
  })

  it('别的工具、普通的输出、别的队友的消息，都不当成它', async () => {
    const { view, socket } = await open()

    socket.emit(live([{ type: 'tool', id: 'b', name: 'Bash', arguments: '{"content":"ls -la' }], 'cheese-b'))
    socket.emit(live([{ type: 'text', text: '自言自语' }], 'cheese-c'))
    socket.emit(live([sending('{"content":"我的那条')]))
    socket.emit({ type: 'assistant_block', block: message('另一位说的', 'cheese-d') })
    await flush()

    expect(text(view)).not.toContain('ls -la')
    expect(text(view)).not.toContain('自言自语')
    expect(text(view)).toContain('我的那条')
    expect(text(view)).toContain(TYPING)
  })

  it('连上时那一帧空的，什么都不画', async () => {
    const { view, socket } = await open()

    socket.emit({ type: 'live', turn_id: null, agent: '', blocks: [] })
    await flush()

    expect(text(view)).not.toContain(TYPING)
  })
})
