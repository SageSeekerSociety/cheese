// 房间里谁在忙：socket 上的 `activity` / `activity_snapshot` 变成输入框下面那一行，
// 我自己在输入框里打字变成发给房间的 `typing`。说的永远是某一位成员。
import type { Topic, WsServerFrame } from '@/cx_types'
import type { MemberActivityLine } from '@/lib/memberActivity'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()
const listTopicMembers = vi.fn()

vi.mock('../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../lib/libraryApi')>('../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('@/me', () => ({ myHandle: () => 'me', myId: () => '1' }))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listTopicMembers: (...args: unknown[]) => listTopicMembers(...args),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...args: unknown[]) => listBlocks(...args),
    chatWsUrl: () => 'ws://test/chat',
  }
})

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

const topic = {
  id: 'activity-topic',
  project_id: 'p1',
  parent_id: null,
  title: 'Activity',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
} as Topic

const KIMI = 'cheese-aaaa11112222'
const OPUS = 'cheese-bbbb33334444'
const roster = [
  { member_handle: KIMI, name: 'Kimi', agent: true },
  { member_handle: OPUS, name: 'Opus', agent: true },
]
const members = [{ user_handle: 'alice', name: 'Alice' }]

class FakeWebSocket {
  static OPEN = 1
  static instances: FakeWebSocket[] = []
  readyState = FakeWebSocket.OPEN
  sent: unknown[] = []
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

async function flush() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

function mountPanel() {
  const vuetify = createVuetify({ components, directives })
  return render(ChatPanel, {
    props: { topic, topicList: [topic], members, showComposer: true },
    global: { plugins: [vuetify, i18n] },
  })
}

function said(view: ReturnType<typeof mountPanel>): string[] {
  return Array.from(view.container.querySelectorAll('.member-activity__line')).map((el) => el.textContent?.trim() ?? '')
}

function lastLines(view: ReturnType<typeof mountPanel>): MemberActivityLine[] {
  const calls = (view.emitted('activity') ?? []) as MemberActivityLine[][][]
  return calls.at(-1)?.[0] ?? []
}

beforeEach(() => {
  setLocale('en')
  vi.clearAllMocks()
  FakeWebSocket.instances = []
  listBlocks.mockResolvedValue({ data: [], has_more: false })
  listTopicMembers.mockResolvedValue({ data: roster, total: roster.length })
  vi.stubGlobal('WebSocket', FakeWebSocket)
})
afterEach(() => vi.useRealTimers())

describe('who is busy in the room', () => {
  it('two teammates working are each named under the composer; one finishing drops only it', async () => {
    const view = mountPanel()
    await flush()
    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'activity', member: KIMI, kind: 'working', active: true, since: 1 })
    socket.emit({ type: 'activity', member: OPUS, kind: 'working', active: true, since: 2 })
    await flush()
    expect(said(view).map((s) => s.split(' · ')[0])).toEqual(['Kimi is working…', 'Opus is working…'])
    expect(lastLines(view).map((l) => l.name)).toEqual(['Kimi', 'Opus'])

    socket.emit({ type: 'activity', member: KIMI, kind: 'working', active: false, since: 3 })
    await flush()
    expect(said(view).map((s) => s.split(' · ')[0])).toEqual(['Opus is working…'])
  })

  it('a socket that joins late learns who is busy from the snapshot', async () => {
    const view = mountPanel()
    await flush()
    FakeWebSocket.instances.at(-1)!.emit({
      type: 'activity_snapshot',
      members: [
        { member: OPUS, kind: 'working', since: 1 },
        { member: 'alice', kind: 'typing', since: 2, expires_in: 5 },
      ],
    })
    await flush()
    const shown = said(view)
    expect(shown[0]).toMatch(/^Opus is working…/)
    expect(shown[1]).toBe('Alice is typing…')
  })

  it('a person typing disappears when the ping runs out, without a stop frame', async () => {
    const view = mountPanel()
    await flush()
    vi.useFakeTimers()
    FakeWebSocket.instances
      .at(-1)!
      .emit({ type: 'activity', member: 'alice', kind: 'typing', active: true, since: 1, expires_in: 5 })
    await vi.advanceTimersByTimeAsync(0)
    expect(said(view)).toEqual(['Alice is typing…'])
    await vi.advanceTimersByTimeAsync(5_100)
    expect(said(view)).toEqual([])
  })

  it('my own typing is not shown back to me', async () => {
    const view = mountPanel()
    await flush()
    FakeWebSocket.instances
      .at(-1)!
      .emit({ type: 'activity', member: 'me', kind: 'typing', active: true, since: 1, expires_in: 5 })
    await flush()
    expect(said(view)).toEqual([])
  })

  it('typing in the composer tells the room, and clearing it says I stopped', async () => {
    const view = mountPanel()
    await flush()
    const socket = FakeWebSocket.instances.at(-1)!
    const input = view.container.querySelector('textarea') as HTMLTextAreaElement
    await fireEvent.update(input, 'hel')
    await fireEvent.update(input, 'hello')
    await flush()
    const typing = socket.sent.filter((m) => (m as { type: string }).type === 'typing')
    // At most one ping every few seconds, however fast the keys come.
    expect(typing).toEqual([{ type: 'typing' }])

    await fireEvent.update(input, '')
    await flush()
    expect(socket.sent.at(-1)).toEqual({ type: 'typing', active: false })
  })
})
