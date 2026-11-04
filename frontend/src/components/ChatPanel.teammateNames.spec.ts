// 一间房里坐着几个 AI 队友时，每一句话、每一条通知署的是那一轮的那位队友。
//
// 平台替一轮写的通知（失败、兜底投递）署名是 system，轮次帧和收件人写的是队友自己
// 的 handle（`cheese-kimi`），都不是名册上的座位。认不出是谁的那一轮就不署队友：退回
// 房间的默认 AI，读起来就是另一个队友失败了、另一个队友在干活。
import type { Block, ProjectMemberRow, Topic, TopicMemberRow, WsServerFrame } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

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
import { loadMarkdownReader } from '@/test/markdownReader'

const topic = {
  id: 'room',
  project_id: 'p1',
  parent_id: null,
  title: 'Room',
  kind: 'topic',
  status: 'active',
  created_by: 'me',
  created_at: '2026-09-28T00:00:00Z',
  updated_at: '2026-09-28T00:00:00Z',
} as Topic

// 三位队友：各有名册上的座位，和自己的 handle。默认那位排在房间名册最前。
const TEAM = [
  { seat: 'cheese-0000000000a1', own: 'cheese', name: '芝士', isDefault: true },
  { seat: 'cheese-0000000000b2', own: 'cheese-kimi', name: '芝士K', isDefault: false },
  { seat: 'cheese-0000000000c3', own: 'cheesex-opus-cc', name: '芝士Opus', isDefault: false },
]
const [, KIMI, OPUS] = TEAM

const roster: TopicMemberRow[] = [
  { topic_id: 'room', member_handle: 'me', name: '我', role: 'owner', agent: false },
  ...TEAM.map((a) => ({
    topic_id: 'room',
    member_handle: a.seat,
    name: a.name,
    role: 'member' as const,
    agent: true,
  })),
]
const members: ProjectMemberRow[] = [
  { user_handle: 'me', name: '我' },
  ...TEAM.map((a) => ({
    user_handle: a.seat,
    name: a.name,
    agent: true,
    source: 'agent' as const,
    project_default: a.isDefault,
    instance_handle: a.own,
  })),
]

let at = 0
function block(fields: Partial<Block>): Block {
  at += 1
  return {
    id: `b${at}`,
    project_id: 'p1',
    conversation_id: 'room',
    kind: 'message',
    author_type: 'participant',
    content: '',
    reply_to: null,
    refs: [],
    created_at: new Date(Date.UTC(2026, 8, 28, 0, at)).toISOString(),
    ...fields,
  } as Block
}
// 人把一句话交给某位队友，开了那一轮。
const ask = (to: string, turn: string) =>
  block({ author: 'me', content: '请看一下', meta: { agent_recipient: { handle: to }, consumed_turn: turn } })
const reply = (seat: string, turn: string, content: string) => block({ author: seat, turn_id: turn, content })
// 平台替这一轮写的通知。`seat` 是后端记下的那一轮是谁的。
// 后端那张表给这几类的样子（`notice_meta.shown_as`）：没做完要人来，兜底投递是平台
// 自己的运行。
const SHOWN_AS: Record<string, Block['shown_as']> = { turn_failed: 'for_agent', timed_delivery: 'hidden' }
const notice = (turn: string, eventType: string, who: string, content: string, seat?: string) =>
  block({
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    turn_id: turn,
    content,
    shown_as: SHOWN_AS[eventType] ?? 'happening',
    meta: { event_type: eventType, severity: 'error', who, detail: '服务原话：No response', ...(seat ? { seat } : {}) },
  })

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

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

function mount() {
  return render(ChatPanel, {
    props: { topic, topicList: [topic], members },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

// 每条通知那一行以谁的身份出现：头像是谁的（记号上的 aria-label）、名字那一行写的
// 是谁，和它说的话。平台自己的那一行两样都没有。
function notices(container: Element) {
  return Array.from(container.querySelectorAll('.notice-row')).map((row) => ({
    avatar: row.querySelector('[role="img"]')?.getAttribute('aria-label') ?? null,
    name: row.querySelector('.notice-row__name')?.textContent?.trim() ?? null,
    text: row.textContent ?? '',
  }))
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  at = 0
  FakeWebSocket.instances = []
  listTopicMembers.mockResolvedValue({ data: roster, total: roster.length })
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

beforeAll(loadMarkdownReader)

describe('一间房里几位队友，各署各的名', () => {
  it('回复署那一轮的队友；平台替它说的失败认得出是哪一位，但不署它的名', async () => {
    listBlocks.mockResolvedValue({
      data: [
        ask(KIMI.own, 'kimi-turn'),
        reply(KIMI.seat, 'kimi-turn', 'Kimi 的回复'),
        notice('kimi-turn', 'turn_failed', 'human', 'Kimi 这一轮失败了'),
        ask(OPUS.own, 'opus-turn-1'),
        reply(OPUS.seat, 'opus-turn-1', 'Opus 的回复'),
        // 平台自己的运行：不在对话里。
        ask(OPUS.own, 'opus-turn-2'),
        notice('opus-turn-2', 'timed_delivery', 'cheese', 'Opus 的兜底投递'),
        ask(OPUS.own, 'opus-turn-3'),
        notice('opus-turn-3', 'turn_failed', 'human', 'Opus 这一轮失败了'),
      ],
      has_more: false,
    })

    const { container } = mount()
    await flush()

    const names = Array.from(container.querySelectorAll('.im-name')).map((n) => n.textContent?.trim())
    expect(names).toContain('芝士K')
    expect(names).toContain('芝士Opus')
    const rows = notices(container)
    const find = (text: string) => {
      const row = rows.find((r) => r.text.includes(text))
      return row && { avatar: row.avatar, name: row.name }
    }
    expect(find('Kimi 这一轮失败了')).toEqual({ avatar: '芝士K', name: null })
    expect(find('Opus 这一轮失败了')).toEqual({ avatar: '芝士Opus', name: null })
    expect(container.textContent).not.toContain('Opus 的兜底投递')
  })

  it('一轮还没开始就失败了，认得出它记下的那位队友；说不出是谁的就只是平台的', async () => {
    listBlocks.mockResolvedValue({
      data: [
        // 交给 Kimi 的那一轮没起来：时间线上只有平台替它写的这一条。
        notice('kimi-turn', 'turn_failed', 'human', 'Kimi 这一轮没起来', KIMI.own),
        notice('nobody-turn', 'turn_failed', 'human', '说不出是谁的那一轮'),
      ],
      has_more: false,
    })

    const { container } = mount()
    await flush()

    const rows = notices(container)
    const find = (text: string) => {
      const row = rows.find((r) => r.text.includes(text))
      return row && { avatar: row.avatar, name: row.name }
    }
    expect(find('Kimi 这一轮没起来')).toEqual({ avatar: '芝士K', name: null })
    expect(find('说不出是谁的那一轮')).toEqual({ avatar: null, name: null })
  })

  it('成员动态写的是队友自己的 handle，「谁在干活」报它的名字', async () => {
    listBlocks.mockResolvedValue({ data: [], has_more: false })
    const view = mount()
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'activity', member: KIMI.own, kind: 'working', active: true, since: 1 })
    socket.emit({ type: 'activity', member: OPUS.own, kind: 'working', active: true, since: 2 })
    await flush()

    const calls = (view.emitted('activity') ?? []) as { name: string }[][][]
    const lines = calls.at(-1)?.[0] ?? []
    expect(lines.map((l) => l.name)).toEqual(['芝士K', '芝士Opus'])
  })
})
