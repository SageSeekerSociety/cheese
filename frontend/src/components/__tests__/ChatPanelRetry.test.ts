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

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listProjectLibrary: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    listTopicMembers: vi.fn().mockResolvedValue({ data: [] }),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    summonAgent: (...a: unknown[]) => summonAgent(...a),
    chatWsUrl: () => 'ws://test/ws',
    attachmentRawUrl: () => '',
  }
})

import ChatPanel from '../ChatPanel.vue'

import { t } from '@/i18n'

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
    topic_id: '',
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

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function mountRoom(blocks: Block[]) {
  const topic = room()
  listBlocks.mockResolvedValue({ data: blocks.map((b) => ({ ...b, topic_id: topic.id })), has_more: false })
  const utils = render(ChatPanel, {
    props: { topic, topicList: [topic], showComposer: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await flush()
  return { ...utils, topic }
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = class {
    static OPEN = 1
    readyState = 0
    close() {}
    send() {}
  }
})

beforeEach(() => {
  listBlocks.mockReset()
  summonAgent.mockReset()
  summonAgent.mockResolvedValue({ started: true })
})

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
})
