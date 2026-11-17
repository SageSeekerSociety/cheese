/** 已读：一条消息在前台的页面上露面停够了才算我读到，报上去；没停够、页面在后台都不算。
 * 自己那条谁读了，按后端给的和后来到的 `reads` 帧算。 */
import type { Block, Topic } from '../../cx_types'

import { effectScope, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DWELL_MS, REPORT_EVERY_MS, usePanelReceipts } from './receipts'

const api = vi.hoisted(() => ({
  getMessageReads: vi.fn(),
  reportMessageReads: vi.fn(),
}))
vi.mock('../../api', () => api)

type Entry = Partial<IntersectionObserverEntry> & { target: Element }
let fire: (entries: Entry[]) => void = () => {}

class FakeObserver {
  constructor(callback: (entries: Entry[]) => void) {
    fire = callback
  }
  observe() {}
  unobserve() {}
  disconnect() {}
}

const ROOM = { id: 'room-1', project_id: 'p' } as Topic

function block(id: string, seq: number, author: string): Block {
  return {
    id,
    seq,
    author,
    author_type: 'participant',
    conversation_id: ROOM.id,
    kind: 'message',
    content: id,
    created_at: '2026-11-17T10:00:00Z',
  } as Block
}

function setup(messages: Block[]) {
  const content = document.createElement('div')
  for (const b of messages) {
    const row = document.createElement('div')
    row.dataset.rowId = b.id
    content.appendChild(row)
  }
  const scroller = document.createElement('div')
  scroller.appendChild(content)
  const roster = {
    roomMembers: ref([
      { member_handle: 'me', topic_id: ROOM.id, role: 'member' },
      { member_handle: 'zhou', topic_id: ROOM.id, role: 'member' },
      { member_handle: 'cheese-ab12', topic_id: ROOM.id, role: 'member', agent: true, name: '小研' },
    ]),
    agentSeat: ref({ handle: 'cheese-ab12', label: '小研' }),
    seatByHandle: ref(new Map([['cheese-ab12', { agent: true }]])),
    agentDisplayName: () => '小研',
    nameOfHandle: (h: string) => (h === 'zhou' ? '小周' : h),
    avatarSrc: () => null,
  }
  const scope = effectScope()
  const panel = scope.run(() =>
    usePanelReceipts({
      place: () => ROOM,
      author: 'me',
      messages: ref(messages),
      isMine: (b) => b.author === 'me',
      // The roster's shape is what the composable reads, nothing more.
      roster: roster as never,
      scrollRef: ref(scroller),
      contentRef: ref(content),
    })
  )!
  const rowOf = (id: string) => content.querySelector(`[data-row-id="${id}"]`)!
  const show = (id: string, on: boolean) =>
    fire([
      {
        target: rowOf(id),
        isIntersecting: on,
        intersectionRatio: on ? 1 : 0,
        intersectionRect: { height: on ? 40 : 0 } as DOMRectReadOnly,
        boundingClientRect: { height: 40 } as DOMRectReadOnly,
        rootBounds: { height: 600 } as DOMRectReadOnly,
      },
    ])
  return { panel, show, scope }
}

let visible = true
let focused = true

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  visible = true
  focused = true
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => (visible ? 'visible' : 'hidden'))
  vi.spyOn(document, 'hasFocus').mockImplementation(() => focused)
  api.getMessageReads.mockResolvedValue({ readers: [] })
  api.reportMessageReads.mockImplementation(async (_id: string, ranges: [number, number][]) => ({ ranges }))
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  api.getMessageReads.mockReset()
  api.reportMessageReads.mockReset()
})

function reported(): number[] {
  return api.reportMessageReads.mock.calls.flatMap(([, ranges]: [string, [number, number][]]) =>
    ranges.flatMap(([a, b]) => Array.from({ length: b - a + 1 }, (_, i) => a + i))
  )
}

describe('usePanelReceipts', () => {
  it('reports a message once it stayed on screen for the dwell in a foreground page', async () => {
    const { show, scope } = setup([block('a', 3, 'zhou'), block('b', 4, 'zhou')])
    await nextTick()
    show('a', true)
    await vi.advanceTimersByTimeAsync(DWELL_MS + REPORT_EVERY_MS)
    expect(reported()).toEqual([3])
    scope.stop()
  })

  it('does not count a message that left the screen before the dwell', async () => {
    const { show, scope } = setup([block('a', 3, 'zhou')])
    await nextTick()
    show('a', true)
    await vi.advanceTimersByTimeAsync(DWELL_MS / 2)
    show('a', false)
    await vi.advanceTimersByTimeAsync(DWELL_MS + REPORT_EVERY_MS)
    expect(reported()).toEqual([])
    scope.stop()
  })

  it('does not count what sits on screen while the page is in the background', async () => {
    visible = false
    const { show, scope } = setup([block('a', 3, 'zhou')])
    await nextTick()
    show('a', true)
    await vi.advanceTimersByTimeAsync(DWELL_MS + REPORT_EVERY_MS)
    expect(reported()).toEqual([])

    // Back in front, the message still on screen: now it counts.
    visible = true
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(DWELL_MS + REPORT_EVERY_MS)
    expect(reported()).toEqual([3])
    scope.stop()
  })

  it('tells who has read my message, the AI teammate included, and follows reads as they land', async () => {
    api.getMessageReads.mockResolvedValue({ readers: [{ reader: 'cheese-ab12', ranges: [[1, 5]] }] })
    const mine = block('m', 5, 'me')
    const { panel, scope } = setup([block('a', 3, 'zhou'), mine])
    await vi.advanceTimersByTimeAsync(400)
    let receipt = panel.receiptFor(mine)!
    expect(receipt.read.map((p) => p.handle)).toEqual(['cheese-ab12'])
    expect(receipt.unread.map((p) => p.handle)).toEqual(['zhou'])

    panel.applyFrame({ conversation_id: ROOM.id, reader: 'zhou', ranges: [[4, 6]] })
    receipt = panel.receiptFor(mine)!
    expect(receipt.unread).toEqual([])
    expect(receipt.read.map((p) => p.handle)).toEqual(['zhou', 'cheese-ab12'])
    scope.stop()
  })

  it('shows no receipt on somebody else’s message', async () => {
    const theirs = block('a', 3, 'zhou')
    const { panel, scope } = setup([theirs])
    expect(panel.receiptFor(theirs)).toBeNull()
    scope.stop()
  })
})
