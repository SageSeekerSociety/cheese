// 断线重连时，房间留在屏幕上：消息不清空再长回来，在忙的那一行不先消失，断线期间
// 落下的、撤回的、结束了的就地补上或撤掉。只有换房间才清空。
import type { Block, Topic, WsServerFrame } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()

vi.mock('@/lib/roomLink', () => import('@/test/fakeRoomLink'))
vi.mock('../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../lib/libraryApi')>('../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('@/me', () => ({ myHandle: () => 'me', myId: () => '1' }))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listTopicMembers: vi
      .fn()
      .mockResolvedValue({ data: [{ member_handle: 'cheese-aaaa11112222', name: 'Kimi', agent: true }] }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...args: unknown[]) => listBlocks(...args),
  }
})

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

const KIMI = 'cheese-aaaa11112222'

const topic = {
  id: 'reconnect-topic',
  project_id: 'p1',
  parent_id: null,
  title: 'Reconnect',
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
  sent: { type: string }[] = []
  onmessage: ((event: MessageEvent) => void) | null = null
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor() {
    FakeWebSocket.instances.push(this)
  }
  close() {}
  send(data: string) {
    this.sent.push(JSON.parse(data))
  }
  emit(frame: WsServerFrame) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent)
  }
}

function message(id: string, minute: number, content: string): Block {
  return {
    id,
    project_id: 'p1',
    conversation_id: topic.id,
    kind: 'message',
    author_type: 'participant',
    author: 'alice',
    content,
    created_at: `2026-10-07T10:${String(minute).padStart(2, '0')}:00Z`,
  } as unknown as Block
}

function page(blocks: Block[]) {
  return { data: blocks, has_more: false }
}

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

async function flush() {
  await vi.advanceTimersByTimeAsync(0)
}

function mountPanel() {
  // 每次一间新房：页面级的历史缓存按房间记，同一间房第二次进来就不再出骨架屏。
  const room = { ...topic, id: crypto.randomUUID() }
  return render(ChatPanel, {
    props: { topic: room, topicList: [room], members: [], showComposer: true },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

function textOf(view: ReturnType<typeof mountPanel>, text: string): Element | undefined {
  return Array.from(view.container.querySelectorAll('.im-text')).find((el) => el.textContent?.includes(text))
}

function busyLines(view: ReturnType<typeof mountPanel>): string[] {
  return Array.from(view.container.querySelectorAll('.member-activity__line')).map((el) => el.textContent?.trim() ?? '')
}

/** 现在这条 socket 连上；返回它。 */
async function opened(): Promise<FakeWebSocket> {
  const socket = FakeWebSocket.instances.at(-1)!
  socket.onopen?.()
  await flush()
  return socket
}

/** 现在这条 socket 断掉，等重连去读历史。 */
async function drop(socket: FakeWebSocket) {
  socket.onclose?.()
  await vi.advanceTimersByTimeAsync(1000)
}

beforeEach(() => {
  setLocale('en')
  vi.useFakeTimers()
  FakeWebSocket.instances = []
  listBlocks.mockReset()
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('a reconnect keeps the room on screen', () => {
  it('keeps the messages while history is re-read, then adds what landed while away', async () => {
    listBlocks.mockResolvedValue(page([message('m1', 1, 'first words')]))
    const view = mountPanel()
    await flush()
    const socket = await opened()
    // 连着的时候 socket 推来一条：它只在屏幕上，不在打开房间时读的那一页里。
    socket.emit({ type: 'user_block', block: message('m2', 2, 'pushed live') })
    await flush()
    const row = textOf(view, 'pushed live')
    expect(row).toBeTruthy()

    const reread = deferred<ReturnType<typeof page>>()
    listBlocks.mockReturnValue(reread.promise)
    await drop(socket)
    // 历史还在路上：屏幕上照旧是那两条，还是同一个元素。
    expect(textOf(view, 'first words')).toBeTruthy()
    expect(textOf(view, 'pushed live')).toBe(row)

    reread.resolve(
      page([message('m1', 1, 'first words'), message('m2', 2, 'pushed live'), message('m3', 3, 'said while away')])
    )
    await flush()
    expect(textOf(view, 'said while away')).toBeTruthy()
    expect(textOf(view, 'pushed live')).toBe(row)
  })

  it('takes away a message retracted while the link was down', async () => {
    listBlocks.mockResolvedValue(page([message('m1', 1, 'kept'), message('m2', 2, 'retracted later')]))
    const view = mountPanel()
    await flush()
    const socket = await opened()
    expect(textOf(view, 'retracted later')).toBeTruthy()

    listBlocks.mockResolvedValue(page([message('m1', 1, 'kept')]))
    await drop(socket)
    await flush()
    expect(textOf(view, 'retracted later')).toBeUndefined()
    expect(textOf(view, 'kept')).toBeTruthy()
  })

  it('keeps who is working through the drop, and drops them once the room says they stopped', async () => {
    listBlocks.mockResolvedValue(page([message('m1', 1, 'hello')]))
    const view = mountPanel()
    await flush()
    const first = await opened()
    first.emit({ type: 'activity_snapshot', members: [{ member: KIMI, kind: 'working', since: 1 }] })
    await flush()
    expect(busyLines(view)).toHaveLength(1)

    await drop(first)
    await flush()
    const second = FakeWebSocket.instances.at(-1)!
    expect(second).not.toBe(first)
    // 新的一条还没连上、现场还没核对：那一行留着。
    expect(busyLines(view)).toHaveLength(1)

    second.onopen?.()
    await flush()
    expect(second.sent).toContainEqual({ type: 'sync' })
    second.emit({ type: 'room_state', turn_ids: [], members: [] })
    await flush()
    expect(busyLines(view)).toEqual([])
  })
  it('a drop before the room finished opening opens it again, so the skeleton goes away', async () => {
    const first = deferred<ReturnType<typeof page>>()
    listBlocks.mockReturnValue(first.promise)
    const view = mountPanel()
    await flush()
    const socket = FakeWebSocket.instances.at(-1)!
    expect(view.container.querySelectorAll('.skel').length, 'opening shows the skeleton').toBeGreaterThan(0)
    listBlocks.mockResolvedValue(page([message('m1', 1, 'arrived late')]))
    await drop(socket)
    await vi.advanceTimersByTimeAsync(5000)
    expect(textOf(view, 'arrived late')).toBeTruthy()
    expect(view.container.querySelectorAll('.skel')).toHaveLength(0)
    first.resolve(page([]))
  })
})
