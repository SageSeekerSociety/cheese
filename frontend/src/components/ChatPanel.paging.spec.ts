// History paging: one screenful at a time, and the reader must not be yanked.
//
// The panel holds a WINDOW of the timeline (newest PAGE_SIZE blocks), not the
// whole thing — a real topic was 2226 rows / 2.1 MB in one response. Older
// blocks arrive only when someone scrolls up to them, and the page that lands
// goes in ABOVE the viewport, so scrollTop has to be pushed down by exactly the
// height it added. Get that wrong and every page load throws the reader
// upwards, which re-triggers the loader — the failure this window was built to
// avoid.
//
// happy-dom does no layout: scrollHeight / clientHeight / scrollTop are all 0
// and ResizeObserver does not exist. Both are put in by the cases here, so
// "the viewport did not move" is a number this file set rather than a layout
// nobody ran (same method as ChatPanel.keyboardScroll.spec.ts).
import type { BlockPage } from '../api'
import type { Block, Topic } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()

vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  listBlocks: (...args: unknown[]) => listBlocks(...args),
  listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listTopicMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  chatWsUrl: () => 'ws://test/chat',
}))

import { PAGE_SIZE } from '../lib/blockPaging'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

const TOPIC = {
  id: 't-paging',
  project_id: 'p1',
  parent_id: null,
  title: 'Paging',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
} as Topic

function said(id: string): Block {
  return {
    id,
    topic_id: TOPIC.id,
    kind: 'message',
    author_type: 'participant',
    author: 'someone-else',
    content: `message ${id}`,
    created_at: '2026-09-01T10:00:00Z',
  } as Block
}

function page(ids: string[], older: boolean): BlockPage {
  return {
    data: ids.map((id) => said(id)),
    total: 100,
    has_more: older,
    oldest_id: ids[0] ?? null,
    has_newer: false,
    newest_id: ids.at(-1) ?? null,
  }
}

/** The newest page: n1 is its oldest, and there is more above it. */
const NEWEST = () => page(['n1', 'n2', 'n3'], true)
const OLDER = (ids: string[], more = false) => page(ids, more)

/** 一条不露面的事件（`in_room:false`）：占一行块，聊天区画不出任何东西。 */
function hiddenEvent(id: string): Block {
  return {
    id,
    topic_id: TOPIC.id,
    kind: 'event',
    author_type: 'system',
    author: 'platform',
    content: `hidden ${id}`,
    created_at: '2026-09-01T10:00:00Z',
    meta: { in_room: false, event_type: 'cloud_provisioning' },
  } as unknown as Block
}

/** 一整页都不露面的事件。 */
function hiddenPage(ids: string[], older: boolean): BlockPage {
  return {
    data: ids.map((id) => hiddenEvent(id)),
    total: 100,
    has_more: older,
    oldest_id: ids[0] ?? null,
    has_newer: false,
    newest_id: ids.at(-1) ?? null,
  }
}

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

class FakeResizeObserver {
  static instances: FakeResizeObserver[] = []

  targets: Element[] = []
  private readonly cb: ResizeObserverCallback

  constructor(cb: ResizeObserverCallback) {
    this.cb = cb
    FakeResizeObserver.instances.push(this)
  }

  observe(el: Element) {
    this.targets.push(el)
  }

  unobserve() {}

  disconnect() {
    this.targets = []
  }

  fire() {
    this.cb([], this as unknown as ResizeObserver)
  }
}

type Metrics = { scrollHeight: number; clientHeight: number; scrollTop: number }

/** happy-dom has no layout: these three are the browser's numbers, pinned here. */
function fakeMetrics(el: HTMLElement, initial: Metrics): Metrics {
  const m = { ...initial }
  for (const key of ['scrollHeight', 'clientHeight', 'scrollTop'] as const) {
    Object.defineProperty(el, key, {
      configurable: true,
      get: () => m[key],
      set: (v: number) => {
        m[key] = v
      },
    })
  }
  return m
}

/**
 * Content height follows the rows actually on the page: each row added after this
 * call makes the pane `perRow` taller, the moment it is rendered and not before —
 * which is when the browser would report it.
 */
function growWithRows(pane: HTMLElement, m: Metrics, perRow: number) {
  const rows = () => pane.querySelectorAll('[data-mid]').length
  const start = rows()
  const base = m.scrollHeight
  Object.defineProperty(pane, 'scrollHeight', {
    configurable: true,
    get: () => base + (rows() - start) * perRow,
  })
}

async function flush(times = 6) {
  for (let i = 0; i < times; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

let vuetify: ReturnType<typeof createVuetify>
let rooms = 0

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  vi.clearAllMocks()
  FakeResizeObserver.instances = []
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  vi.stubGlobal(
    'WebSocket',
    class {
      static OPEN = 1
      readyState = 1
      onmessage: unknown = null
      onopen: unknown = null
      onclose: unknown = null
      onerror: unknown = null
      close() {}
      send() {}
    }
  )
  // An unread count and the author filter both read the signed-in handle; an
  // empty one silently changes what "someone else said it" means.
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
  listBlocks.mockReset().mockImplementation(async (_topicId, opts: { before?: string } = {}) => {
    if (opts.before) return OLDER([])
    return NEWEST()
  })
})

/** Mount with a pane taller than the content (the ordinary case: 2000 in 500). */
async function mount(
  metrics: Metrics = { scrollHeight: 2000, clientHeight: 500, scrollTop: 2000 },
  mixins: object[] = []
) {
  // One room per case: the block window cache and the saved scroll position are
  // kept per topic id at module scope (they survive leaving a topic), so a
  // shared id would hand the next case the previous one's history.
  const topic: Topic = { ...TOPIC, id: `t-paging-${++rooms}` }
  const view = render(ChatPanel, {
    props: { topic, topicList: [topic] },
    global: { plugins: [vuetify, i18n], mixins },
  })
  const pane = view.container.querySelector<HTMLElement>('[data-testid="chat-scroll"]')
  expect(pane, 'the chat pane must be there for a scroll to be dispatched at it').toBeTruthy()
  const m = fakeMetrics(pane!, metrics)
  await flush()
  return { view, container: view.container, pane: pane!, m }
}

const shown = (container: Element) =>
  Array.from(container.querySelectorAll('[data-mid]')).map((el) => el.getAttribute('data-mid'))
const olderLoader = (container: Element) => container.querySelector('[data-testid="chat-older-loader"]')

function scrollToTop(pane: HTMLElement, m: Metrics) {
  m.scrollTop = 0
  pane.dispatchEvent(new Event('scroll'))
}

function beforeCalls(): string[] {
  return listBlocks.mock.calls
    .map(([, opts]) => opts as { before?: string })
    .filter((o) => o?.before)
    .map((o) => o.before!)
}

describe('往回翻历史', () => {
  it('打开时只读最近一页，上面还有更早的就说出来', async () => {
    const { container } = await mount()

    expect(shown(container)).toEqual(['n1', 'n2', 'n3'])
    expect(listBlocks.mock.calls).toHaveLength(1)
    expect(listBlocks.mock.calls[0][1]).toEqual({ limit: PAGE_SIZE })
    expect(olderLoader(container)?.textContent?.trim()).toBe('更早的消息')
  })

  it('滚到顶上才拉更早的一页，插进来的一页不能把读的人顶走', async () => {
    const { container, pane, m } = await mount()
    expect(shown(container)).toEqual(['n1', 'n2', 'n3'])

    const gate = deferred<BlockPage>()
    listBlocks.mockImplementationOnce(async () => gate.promise)
    scrollToTop(pane, m)
    await flush()

    expect(beforeCalls(), '上滚才拉，游标是最老的那一条').toEqual(['n1'])

    // The older page lands ABOVE the viewport: the content grew by 1000, so
    // scrollTop has to move down by that much for the reader to stay put.
    growWithRows(pane, m, 500)
    gate.resolve(OLDER(['o1', 'o2']))
    await flush()

    expect(shown(container)).toEqual(['o1', 'o2', 'n1', 'n2', 'n3'])
    expect(m.scrollTop).toBe(1000)
  })

  it('一页在飞的时候还在往上滑：落下来时留在滑到的地方，不被拽回请求发出时的位置', async () => {
    const { container, pane, m } = await mount()

    const gate = deferred<BlockPage>()
    listBlocks.mockImplementationOnce(async () => gate.promise)
    // A flick: the loader fires near the top, and the pane keeps gliding up
    // while the page is on its way.
    m.scrollTop = 120
    pane.dispatchEvent(new Event('scroll'))
    await flush()
    expect(beforeCalls()).toEqual(['n1'])
    m.scrollTop = 30

    growWithRows(pane, m, 500)
    gate.resolve(OLDER(['o1', 'o2']))
    await flush()

    expect(shown(container)).toEqual(['o1', 'o2', 'n1', 'n2', 'n3'])
    expect(m.scrollTop, 'where the reader had glided to, pushed down by the 1000 that went in above').toBe(1030)
  })

  it('拼进来的一页不让已经在屏上的消息重画', async () => {
    // A page lands above hundreds of rows in a long topic. Each row already on
    // screen that renders again costs main-thread time, and that cost grows with
    // the history read so far — the stall felt while scrolling up.
    const redrawn: string[] = []
    const { container, pane, m } = await mount(undefined, [
      {
        beforeUpdate(this: { $props: { block?: Block } }) {
          const id = this.$props.block?.id
          if (id) redrawn.push(id)
        },
      },
    ])

    listBlocks.mockImplementationOnce(async () => OLDER(['o1', 'o2']))
    growWithRows(pane, m, 500)
    scrollToTop(pane, m)
    await flush()

    expect(shown(container)).toEqual(['o1', 'o2', 'n1', 'n2', 'n3'])
    // n1 now continues o2's run and draws without its header: that one redraw is
    // the page's own doing.
    expect(redrawn.filter((id) => id === 'n2' || id === 'n3')).toEqual([])
  })

  it('一页在飞的时候再滚一次不会把同一个游标问第二遍', async () => {
    const { pane, m } = await mount()

    const gate = deferred<BlockPage>()
    listBlocks.mockImplementationOnce(async () => gate.promise)
    scrollToTop(pane, m)
    await flush()
    scrollToTop(pane, m)
    await flush()

    expect(beforeCalls()).toEqual(['n1'])

    growWithRows(pane, m, 500)
    gate.resolve(OLDER(['o1', 'o2']))
    await flush()
    expect(beforeCalls()).toEqual(['n1'])
  })

  it('没有更早的了：不画那一行，也不再请求', async () => {
    listBlocks.mockReset().mockImplementation(async () => page(['n1', 'n2', 'n3'], false))
    const { container, pane, m } = await mount()

    expect(olderLoader(container), '到顶了就不该再装作还有').toBeNull()
    scrollToTop(pane, m)
    await flush()
    expect(listBlocks.mock.calls).toHaveLength(1)
  })

  it('第一页填不满一屏时自己接着拉，直到能滚', async () => {
    // A page of very short messages can be shorter than the pane. Then there is
    // nothing to scroll, no scroll event ever fires, and the rest of the
    // history would be unreachable.
    listBlocks.mockReset().mockImplementation(async (_topicId, opts: { before?: string } = {}) => {
      if (opts.before === 'n1') return OLDER(['m1', 'm2'], true)
      if (opts.before === 'm1') return OLDER(['z1'], false)
      return NEWEST()
    })
    const { container } = await mount({ scrollHeight: 100, clientHeight: 500, scrollTop: 100 })

    await waitFor(() => expect(beforeCalls()).toEqual(['n1', 'm1']))
    expect(shown(container)).toEqual(['z1', 'm1', 'm2', 'n1', 'n2', 'n3'])
    expect(olderLoader(container), '读不到更早的了，那一行就收起').toBeNull()
  })

  it('最近一页整页都不露面：窗口里一条可见的都没有，也照样接着往上拉', async () => {
    // 事件远多于消息的房间里（「cheese 前端架构改造调研」21685 块，尾巴连着几十条
    // in_room:false 的事件），最新那一页可以整页都画不出东西。窗口里一条可见的都没有
    // 时游标不能不认——游标是「读到哪了」，不是「画得出什么」，否则连着拉都发不出去，
    // 房间开出来一片空白。
    listBlocks.mockReset().mockImplementation(async (_topicId, opts: { before?: string } = {}) => {
      if (opts.before === 'h1') return OLDER(['o1', 'o2'], false)
      return hiddenPage(['h1', 'h2', 'h3'], true)
    })
    const { container } = await mount({ scrollHeight: 100, clientHeight: 500, scrollTop: 100 })

    await waitFor(() => expect(beforeCalls()).toEqual(['h1']))
    expect(shown(container)).toEqual(['o1', 'o2'])
  })

  it('更早的一页拉失败：说一句，并且不接着往下拉', async () => {
    const { container, pane, m } = await mount()

    listBlocks.mockImplementationOnce(async () => {
      throw new Error('boom')
    })
    scrollToTop(pane, m)
    await flush()

    expect(container.textContent).toContain('boom')
    // A failure must not become a retry loop: the flag is cleared, the top-up
    // pass is skipped, and the next scroll is the reader's own idea.
    expect(beforeCalls()).toEqual(['n1'])
  })
})
