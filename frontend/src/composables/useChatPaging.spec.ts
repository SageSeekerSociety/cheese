/** 向上翻页「不跳」：把上一页拼进来时，scrollTop 要按 scrollHeight 的差补回去
 *  （lib/blockPaging 的 scrollTopAfterPrepend）。这一窗里的行带着 content-visibility
 *  （room-row.css 的 .im-row），离屏的行只报**估计**高度——按估计高度补，补进去的就是
 *  错的，翻页会跳。测量帧让这一窗按真实高度铺开，量完再撤（lib/contentVisibility）。
 *
 *  这里用一个会记账的假滚动容器把这条锁住：
 *   1. 读 scrollHeight（量 before、量 after）一次都不能落在测量帧外——否则量到的是估计高度；
 *   2. scrollTop 落在 before.scrollTop + (after − before)；
 *   3. 测量帧是异步撤的（两个 rAF），不是量完立刻撤，好让这一帧先被画一次。 */
import type { Ref } from 'vue'
import type { useTimeline } from '../components/room/composables/useTimeline'
import type { Topic } from '../cx_types'

import { ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ listBlocks: vi.fn() }))
vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  listBlocks: mocks.listBlocks,
}))
vi.mock('../lib/blockCache', () => ({ setCachedWindow: vi.fn() }))

import { MEASURE_CLASS } from '../lib/contentVisibility'

import { useChatPaging } from './useChatPaging'

/** 假滚动容器：读出 scrollHeight 时记一笔「这一刻测量帧在不在」。 */
function makeScroller(opts: { height?: number; skelHeight?: number } = {}) {
  const classes = new Set<string>()
  const reads: boolean[] = []
  let height = opts.height ?? 1000
  let top = 120
  // 开场骨架（LoadingSkeleton 的 .skel）：占着一屏的形状，但还不是历史。
  const skels = opts.skelHeight ? [{ offsetHeight: opts.skelHeight }] : []
  return {
    classList: {
      add: (c: string) => void classes.add(c),
      remove: (c: string) => void classes.delete(c),
      contains: (c: string) => classes.has(c),
    },
    get scrollTop() {
      return top
    },
    set scrollTop(v: number) {
      top = v
    },
    get scrollHeight() {
      reads.push(classes.has(MEASURE_CLASS))
      return height
    },
    set scrollHeight(v: number) {
      height = v
    },
    clientHeight: 500,
    querySelectorAll: (sel: string) => (sel === '.skel' ? skels : []),
    classes,
    reads,
    skels,
  }
}

/** 拼一页进窗口的假 timeline；`add` 说这一页多画出来了几行（0＝整页都不露面）。 */
function makeTimeline(
  scroller: ReturnType<typeof makeScroller>,
  opts: { add?: () => number; grow?: number; hasMore?: boolean; moreAfter?: boolean } = {}
) {
  const add = opts.add ?? (() => 1)
  const timeline = {
    messages: ref([]),
    hasMore: ref(opts.hasMore ?? true),
    hasNewer: ref(false),
    oldestLoaded: () => 'cursor',
    newest: () => ({ blocks: [], hasMore: false }),
    find: () => undefined,
    showMiddle: vi.fn(),
    backToNewest: vi.fn(),
    prepend: vi.fn(() => {
      scroller.scrollHeight += opts.grow ?? 600
      timeline.hasMore.value = opts.moreAfter ?? true
      return add()
    }),
    appendNewer: vi.fn(),
    capNewest: vi.fn(),
  }
  return timeline
}

function setUp(
  scroller: ReturnType<typeof makeScroller>,
  timeline: ReturnType<typeof makeTimeline> = makeTimeline(scroller),
  loadingHistory = ref(false)
) {
  return useChatPaging({
    topic: () => ({ id: 't1' }) as unknown as Topic,
    focusBlock: () => null,
    timeline: timeline as unknown as ReturnType<typeof useTimeline>,
    scrollRef: ref(scroller) as unknown as Ref<HTMLElement | null>,
    atBottom: ref(false),
    loadingHistory,
    unseen: ref([]),
    errorMsg: ref(null),
    rememberScroll: () => {},
    scrollToMessage: () => {},
    scrollToBottom: () => {},
  })
}

describe('useChatPaging 向上翻页的补偿', () => {
  afterEach(() => vi.restoreAllMocks())

  it('整段量、补都在测量帧里，量完异步撤', async () => {
    const scroller = makeScroller()
    const frames: FrameRequestCallback[] = []
    vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((cb) => {
      frames.push(cb)
      return frames.length
    })
    mocks.listBlocks.mockResolvedValue({ data: [{ id: 'older' }], has_more: true })

    const paging = setUp(scroller)
    await paging.loadOlder()

    // 补的是真实高度：scrollTop = 120 + 600。
    expect(scroller.scrollTop).toBe(720)
    // 读过 scrollHeight，而且**没有一次**落在测量帧外 —— 量的是真实高度，不是估计值。
    expect(scroller.reads.length).toBeGreaterThanOrEqual(2)
    expect(scroller.reads.every(Boolean)).toBe(true)
    // 量完还在测量帧里：撤销排在两个 rAF 之后，先让这一帧按真实高度画一次。
    expect(scroller.classes.has(MEASURE_CLASS)).toBe(true)
    frames.shift()?.(0)
    frames.shift()?.(0)
    expect(scroller.classes.has(MEASURE_CLASS)).toBe(false)
  })

  it('拉回来的是空页（没长高）时不硬补，也不留测量帧', async () => {
    const scroller = makeScroller()
    const frames: FrameRequestCallback[] = []
    vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((cb) => {
      frames.push(cb)
      return frames.length
    })
    // 没长高、也没画出行（页面为空、历史到头）：差是 0，scrollTop 不动。
    const timeline = makeTimeline(scroller, { add: () => 0, grow: 0, moreAfter: false })
    mocks.listBlocks.mockResolvedValue({ data: [], has_more: false })
    const paging = setUp(scroller, timeline)

    await paging.loadOlder()

    expect(scroller.scrollTop).toBe(120)
    frames.shift()?.(0)
    frames.shift()?.(0)
    expect(scroller.classes.has(MEASURE_CLASS)).toBe(false)
  })

  it('一页画不出行就往回接着翻，直到有一页接上看得见的行', async () => {
    // 最新那一段几乎全是 in_room:false 的回合事件：前两页一行都画不出来（added 0），
    // 第三页才接上一条看得见的消息。只翻一页就停的话，这两页等于白翻、翻页也停在那——
    // 读一页没让任何东西长高，就没有下一次滚动事件。
    const scroller = makeScroller()
    let pulls = 0
    const timeline = makeTimeline(scroller, {
      add: () => (++pulls >= 3 ? 1 : 0),
      grow: 0,
      moreAfter: true,
    })
    mocks.listBlocks.mockResolvedValue({ data: [{ id: 'hidden' }], has_more: true })
    const paging = setUp(scroller, timeline)

    await paging.loadOlder()

    expect(mocks.listBlocks).toHaveBeenCalledTimes(3)
  })

  it('连着画不出行也有上限，不会在真没有消息的地方一直翻', async () => {
    const scroller = makeScroller()
    const timeline = makeTimeline(scroller, { add: () => 0, grow: 0, moreAfter: true })
    mocks.listBlocks.mockResolvedValue({ data: [{ id: 'hidden' }], has_more: true })
    const paging = setUp(scroller, timeline)

    await paging.loadOlder()

    // 有界：翻了不止一页，但没到无限。
    expect(mocks.listBlocks.mock.calls.length).toBeGreaterThan(1)
    expect(mocks.listBlocks.mock.calls.length).toBeLessThanOrEqual(24)
  })

  it('翻页在飞的时候这一段被整段换过（游标变了），这一页丢掉不接', async () => {
    // 开场那条请求（或是 `?block=` 的跳转）在翻页请求还没回来时把整段换掉了：拿旧游标
    // 读回来的这一页接到新一段上，中间会缺一段——看着就是「跳了一下、中间空一格」。
    const scroller = makeScroller()
    let cursor = 'cursor'
    const timeline = makeTimeline(scroller)
    timeline.oldestLoaded = () => cursor
    mocks.listBlocks.mockImplementation(async () => {
      cursor = 'reset' // 换过一段了，游标不作数
      return { data: [{ id: 'older' }], has_more: true }
    })
    const paging = setUp(scroller, timeline)

    await paging.loadOlder()

    expect(timeline.prepend).not.toHaveBeenCalled()
    expect(scroller.scrollTop).toBe(120)
  })

  it('开场骨架占着一屏时不算铺满：先补历史，补满了才露出来', async () => {
    // 骨架（.skel）高 200、这一窗历史高 400：scrollHeight 读到 600，比容器 500 还高——
    // 按它判「铺满」就会在只画得出 400px 历史时撤掉骨架，最新那条飘在半空。骨架不算历史。
    const scroller = makeScroller({ height: 600, skelHeight: 200 })
    const timeline = makeTimeline(scroller, { add: () => 1, grow: 150, moreAfter: true })
    mocks.listBlocks.mockResolvedValue({ data: [{ id: 'older' }], has_more: true })
    const paging = setUp(scroller, timeline, ref(true))

    await paging.fillViewportIfNeeded()

    expect(mocks.listBlocks).toHaveBeenCalledTimes(1)
  })
})
