/** 失败提示上的「重试」。
 *
 * 一轮失败之后，人要的是再来一次，而不是去猜该 @ 谁。所以可以重试的失败提示
 * 自己带一个按钮，点下去和「交给它」走同一个入口。只有最新的那一条有：更早的
 * 失败已经被后面的事盖过去了。
 */
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()
const summonAgent = vi.fn()

vi.mock('../../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../../lib/libraryApi')>('../../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    listTopicMembers: vi.fn().mockResolvedValue({ data: [] }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    summonAgent: (...a: unknown[]) => summonAgent(...a),
    attachmentRawUrl: () => '',
  }
})

import ChatPanel from '../ChatPanel.vue'

import i18n, { t } from '@/i18n'
import { loadMarkdownReader } from '@/test/markdownReader'

// 按钮的名字从文案目录取：改字不该让这份测试变红。
const RETRY = () => ({ name: t('work.room.retry.action') })

let seq = 0
function room(): Topic {
  seq += 1
  return {
    id: `retry-room-${seq}`,
    project_id: 'p1',
    parent_id: null,
    title: '重试',
    kind: 'topic',
    status: 'active',
    created_at: '2026-09-25T00:00:00Z',
  }
}

let blockSeq = 0
function block(over: Partial<Block>): Block {
  blockSeq += 1
  return {
    id: `b-${blockSeq}`,
    conversation_id: '',
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content: '',
    meta: null,
    created_at: `2026-09-25T10:${String(blockSeq).padStart(2, '0')}:00Z`,
    ...over,
  }
}

function failure(retryable: boolean): Block {
  return block({
    content: '本轮未完成：AI 服务返回错误',
    meta: {
      event_type: 'turn_failed',
      severity: 'error',
      who: 'human',
      detail: '服务原话',
      detail_label: '详细说明',
      ...(retryable ? { retryable: true } : {}),
    },
  })
}

function message(): Block {
  return block({ kind: 'message', author_type: 'participant', author: 'alice', content: '再看看' })
}

/** 会话没起来的那张事故卡（真事件见 2026-10-22 的 session_start_work_machine_refused）。 */
function incident(turnId?: string): Block {
  return block({
    content: 'Claude Code 启动失败：没能取得这个频道的环境',
    turn_id: turnId ?? null,
    meta: {
      event_type: 'platform_error',
      code: 'session_start_work_machine_refused',
      severity: 'error',
      title: '会话没有启动',
      retryable: true,
    },
  })
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

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

  emit(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent)
  }
}

async function mountRoom(blocks: Block[]) {
  const topic = room()
  listBlocks.mockResolvedValue({ data: blocks.map((b) => ({ ...b, conversation_id: topic.id })), has_more: false })
  const utils = render(ChatPanel, {
    props: { topic, topicList: [topic], showComposer: true },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await flush()
  return { ...utils, topic, socket: FakeWebSocket.instances.at(-1)! }
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket
})

beforeEach(() => {
  FakeWebSocket.instances = []
  listBlocks.mockReset()
  summonAgent.mockReset()
  summonAgent.mockResolvedValue({ started: true })
})

beforeAll(loadMarkdownReader)

describe('失败提示上的重试', () => {
  it('最新一条可以重试的失败带一个按钮，点下去重新开一轮', async () => {
    const { queryByRole, topic } = await mountRoom([message(), failure(true)])

    const retry = queryByRole('button', RETRY())
    expect(retry).not.toBeNull()
    await fireEvent.click(retry!)
    await flush()

    expect(summonAgent).toHaveBeenCalledWith(topic.id)
  })

  it('重试没有用的失败不给按钮', async () => {
    const { queryByRole } = await mountRoom([message(), failure(false)])
    expect(queryByRole('button', RETRY())).toBeNull()
  })

  it('后面已经有新消息的旧失败不给按钮', async () => {
    const { queryByRole } = await mountRoom([failure(true), message()])
    expect(queryByRole('button', RETRY())).toBeNull()
  })

  it('点下去之后房间就在等回复，按钮不再出现', async () => {
    const { queryByRole } = await mountRoom([message(), failure(true)])
    await fireEvent.click(queryByRole('button', RETRY())!)
    await flush()
    expect(queryByRole('button', RETRY())).toBeNull()
  })

  it('会话没起来的事故卡也带按钮，点了就重开一轮', async () => {
    // 这条守的是这次改动赖以为前提的那件事 —— 事故卡上的按钮点得进去、点完房间
    // 就在等回复。它自己不新（改动之前就是这个行为），所以代码全回退它照样绿；
    // 「按钮什么时候出现」由下面那条守。
    const { queryByRole, topic } = await mountRoom([message(), incident()])

    const retry = queryByRole('button', RETRY())
    expect(retry).not.toBeNull()
    await fireEvent.click(retry!)
    await flush()

    expect(summonAgent).toHaveBeenCalledWith(topic.id)
    expect(queryByRole('button', RETRY())).toBeNull()
  })

  it('点不到重试的时候，不摆那句「可以重试」', async () => {
    // 事故卡后面又来了话：这张卡已经过去，房间不会在它上面重试。此时只画一句
    // 「可以重试」等于给了一个点不动的按钮 —— 点了什么都没发生，人只会觉得坏了。
    const { queryByRole, queryByText } = await mountRoom([incident(), message()])
    expect(queryByRole('button', RETRY())).toBeNull()
    expect(queryByText(t('work.room.notice.incident.retryable'))).toBeNull()
  })

  it('点了重试、服务端说房间还在跑，就明说', async () => {
    summonAgent.mockResolvedValue({ started: false, reason: 'working' })
    const { queryByRole, findByText } = await mountRoom([message(), failure(true)])
    await fireEvent.click(queryByRole('button', RETRY())!)
    await flush()
    expect(await findByText(t('work.room.retry.working'))).toBeTruthy()
  })

  it('会话没起来的失败事件一到，房间就不再等它，重试当场能点', async () => {
    // 会话压根没起来，就不会有 turn_finished（那一帧的发出要这一轮宣布过自己开始）。
    // 只等 error/done 里的 settleIfIdle 清不掉「还有一轮在跑」：房间会一直装作芝士
    // 在干活，失败卡上那颗重试要等后端每 15 秒一次的对账才对上。
    const { queryByRole, socket } = await mountRoom([message()])
    socket.emit({ type: 'turn_started', turn_id: 't1', agent: 'cheese' })
    await flush()
    expect(queryByRole('button', RETRY())).toBeNull()

    socket.emit({ type: 'event_block', block: incident('t1') })
    await flush()

    expect(queryByRole('button', RETRY())).not.toBeNull()
  })
})
