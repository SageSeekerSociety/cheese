/**
 * 队友还在干活时离开房间、再回来：它那张在跑的清单还找得到，正在做的那一步还在转。
 *
 * 离开的这段时间里队友又落了不止一页的块。回来时 broker 先报「此刻在跑的这几轮」，
 * 再把这一轮缓存的帧整段重放一遍（routes/chat.py、runtime.py 的 publish）；历史
 * 那边读的是最新的一页。清单比这一页早，往上翻就该翻到它。
 */
import type { Component } from 'vue'
import type { Block, Topic } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listBlocks: vi.fn(),
  listRoomTasks: vi.fn().mockResolvedValue({ data: [] }),
  listTopicMembers: vi.fn().mockResolvedValue({ data: [] }),
  getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
  chatWsUrl: (id: string) => `ws://test/chat/${id}`,
}))

import { listBlocks } from '../api'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

const sockets: TestSocket[] = []
class TestSocket {
  static OPEN = 1
  readyState = 1
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send = vi.fn()
  close = vi.fn()
  url: string
  constructor(url: string) {
    this.url = url
    sockets.push(this)
  }
  frame(f: unknown) {
    this.onmessage?.({ data: JSON.stringify(f) })
  }
}

const roomA = { id: 'room-a', project_id: 'p', title: 'A', kind: 'topic' } as Topic
const roomB = { id: 'room-b', project_id: 'p', title: 'B', kind: 'topic' } as Topic
const AGENT = 'agent-cheese'

function checklistAt(step: number): Block {
  return {
    id: 'list-1',
    conversation_id: roomA.id,
    kind: 'message',
    author_type: 'participant',
    author: AGENT,
    turn_id: 'turn-1',
    content: '',
    created_at: '2026-10-03T10:00:00.000Z',
    meta: {
      checklist: {
        items: [1, 2, 3].map((i) => ({
          id: String(i),
          subject: `第 ${i} 步`,
          status: i < step ? 'completed' : i === step ? 'in_progress' : 'pending',
        })),
        result: null,
      },
    },
  } as unknown as Block
}
function event(n: number): Block {
  return {
    id: `ev-${n}`,
    conversation_id: roomA.id,
    kind: 'event',
    author_type: 'participant',
    author: AGENT,
    turn_id: 'turn-1',
    content: `step ${n}`,
    created_at: new Date(Date.parse('2026-10-03T10:00:00Z') + (n + 1) * 1000).toISOString(),
    meta: { event: 'tool_use', tool: 'Bash' },
  } as unknown as Block
}

/** The room's stored history, oldest first, paged the way GET /blocks pages it. */
let stored: Block[] = []
function serve(topicId: string, opts?: { limit?: number; before?: string }) {
  const rows = topicId === roomA.id ? stored : []
  const end = opts?.before ? rows.findIndex((b) => b.id === opts.before) : rows.length
  const limit = opts?.limit ?? 50
  const data = rows.slice(Math.max(0, end - limit), end)
  return {
    data,
    has_more: end - limit > 0,
    total: rows.length,
    oldest_id: data[0]?.id ?? null,
    has_newer: false,
    newest_id: null,
  }
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}
const latest = (id: string) => [...sockets].reverse().find((s) => s.url.includes(id))!
const steps = (view: ReturnType<typeof render>) => view.container.querySelectorAll('.checklist li').length
const turning = (view: ReturnType<typeof render>) => view.container.querySelector('.is-live')

async function scrollUp(view: ReturnType<typeof render>) {
  const pane = view.container.querySelector<HTMLElement>('.messages')!
  pane.scrollTop = 0
  pane.dispatchEvent(new Event('scroll'))
  await settle()
}

// A pane already full of rows, as in a real room: nothing tops it up by itself, so
// older pages arrive only when the reader scrolls up.
beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true, get: () => 2000 })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => 600 })
  setLocale('zh-CN')
  sockets.length = 0
  vi.stubGlobal('WebSocket', TestSocket)
  vi.mocked(listBlocks).mockImplementation(async (id, opts) => serve(id, opts))
})
afterEach(() => {
  // The overrides sit on HTMLElement; removing them uncovers jsdom's own on Element.
  delete (HTMLElement.prototype as { scrollHeight?: number }).scrollHeight
  delete (HTMLElement.prototype as { clientHeight?: number }).clientHeight
  cleanup()
  vi.unstubAllGlobals()
})

describe('a running checklist, after you leave the room and come back', () => {
  for (const order of ['replay lands before history', 'replay lands after history'] as const) {
    it(`is still there to scroll up to, and still turning (${order})`, async () => {
      stored = [checklistAt(1), ...Array.from({ length: 10 }, (_, i) => event(i))]
      const view = render(ChatPanel as unknown as Component, {
        props: { topic: roomA, topicList: [roomA, roomB] },
        global: { plugins: [createVuetify({ components, directives }), i18n] },
      })
      await settle()
      const first = latest(roomA.id)
      first.onopen?.()
      first.frame({ type: 'turn_active', turn_ids: ['turn-1'], agents: { 'turn-1': AGENT } })
      // Everything published on the channel while the turn runs is also buffered for replay.
      const buffer: unknown[] = []
      const publish = (f: { type: string; block: Block }, socket?: TestSocket) => {
        if (f.type === 'block_updated') stored = stored.map((b) => (b.id === f.block.id ? f.block : b))
        else stored = [...stored, f.block]
        buffer.push(f)
        socket?.frame(f)
      }
      for (let i = 10; i < 30; i++) publish({ type: 'event_block', block: event(i) }, first)
      publish({ type: 'block_updated', block: checklistAt(2) }, first)
      await settle()
      expect(turning(view)).not.toBeNull()

      await view.rerender({ topic: roomB, topicList: [roomA, roomB] })
      await settle()
      for (let i = 30; i < 140; i++) publish({ type: 'event_block', block: event(i) })
      publish({ type: 'block_updated', block: checklistAt(3) })

      let release!: () => void
      const held = new Promise<void>((r) => (release = r))
      vi.mocked(listBlocks).mockImplementationOnce(async (id, opts) => {
        await held
        return serve(id, opts)
      })
      await view.rerender({ topic: roomA, topicList: [roomA, roomB] })
      await settle()
      const back = latest(roomA.id)
      const replay = () => {
        back.onopen?.()
        back.frame({ type: 'turn_active', turn_ids: ['turn-1'], agents: { 'turn-1': AGENT } })
        for (const f of buffer) back.frame(f)
      }
      if (order === 'replay lands before history') replay()
      release()
      await settle()
      if (order === 'replay lands after history') replay()
      await settle()

      for (let i = 0; i < 6 && !steps(view); i++) await scrollUp(view)
      expect(steps(view), 'scrolling up reaches the checklist').toBe(3)
      expect(turning(view), 'its current step is turning').not.toBeNull()
    })
  }
})
