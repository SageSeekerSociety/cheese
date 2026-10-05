// 芝士摆出来一份东西（`cheese show` / `serve`）时，面板要立刻知道。
//
// 那份东西落成一块 kind=artifact 的卡，由后端从房间这条 socket 推过来。这一栏是
// socket 的家，右栏的「预览」那一格听不到——所以对话栏要往上报一声。没有这一声的
// 时候，预览那一格只能靠轮询发现（实测十几秒）。
import type { Component } from 'vue'
import type { Block, Topic } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listBlocks: vi.fn().mockResolvedValue({ data: [], has_more: false, total: 0, oldest_id: null }),
  listRoomTasks: vi.fn().mockResolvedValue({ data: [] }),
  listTopicMembers: vi.fn().mockResolvedValue({ data: [] }),
  chatWsUrl: () => 'ws://test/chat',
}))

import ChatPanel from './ChatPanel.vue'

import i18n from '@/i18n'

const sockets: TestSocket[] = []
class TestSocket {
  static OPEN = 1
  readyState = 1
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send = vi.fn()
  close = vi.fn()
  constructor() {
    sockets.push(this)
  }
}

const flush = () => vi.advanceTimersByTimeAsync(0)

function block(kind: string, extra: Partial<Block> = {}): Block {
  return {
    id: `b-${kind}`,
    topic_id: 't',
    kind,
    author_type: 'participant',
    author: 'someone-else',
    content: 'report.html',
    created_at: new Date().toISOString(),
    ...extra,
  } as Block
}

function arrive(b: Block, type = 'assistant_block') {
  sockets[0].onmessage?.({ data: JSON.stringify({ type, block: b }) })
}

async function mountRoom() {
  const view = render(ChatPanel as unknown as Component, {
    props: { topic: { id: 't', project_id: 'p', title: 'Room', kind: 'topic' } as Topic },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await flush()
  sockets[0].onopen?.()
  await vi.advanceTimersByTimeAsync(500) // 连上之后的重放追赶期过去
  return view
}

beforeEach(() => {
  vi.useFakeTimers()
  sockets.length = 0
  vi.stubGlobal('WebSocket', TestSocket)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('芝士摆出来一份东西', () => {
  it('socket 上落下一块 artifact 卡 → 往上报一声，让面板立刻看预览', async () => {
    const { emitted } = await mountRoom()
    arrive(block('artifact', { mime_type: 'text/html' }))
    await flush()
    expect(emitted()['preview-shown']).toHaveLength(1)
  })

  it('别人普通的一句话不算——只有摆出来的东西才换当前预览', async () => {
    const { emitted } = await mountRoom()
    arrive(block('message', { content: '看这个 report.html' }))
    await flush()
    expect(emitted()['preview-shown']).toBeUndefined()
  })

  it('一块 event 卡也不是——它不是指向预览的东西', async () => {
    const { emitted } = await mountRoom()
    arrive(block('event', { content: 'tool', meta: { tool: 'Bash' } }), 'event_block')
    await flush()
    expect(emitted()['preview-shown']).toBeUndefined()
  })
})
