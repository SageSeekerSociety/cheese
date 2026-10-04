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
function makeScroller() {
  const classes = new Set<string>()
  const reads: boolean[] = []
  let height = 1000
  let top = 120
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
    classes,
    reads,
  }
}

function setUp(scroller: ReturnType<typeof makeScroller>) {
  // prepend 把上一页拼了进来：这一窗长高了 600px。
  const timeline = {
    messages: ref([]),
    hasMore: ref(true),
    hasNewer: ref(false),
    oldestLoaded: () => 'cursor',
    newest: () => ({ blocks: [], hasMore: false }),
    find: () => undefined,
    showMiddle: vi.fn(),
    backToNewest: vi.fn(),
    prepend: vi.fn(() => {
      scroller.scrollHeight += 600
    }),
    appendNewer: vi.fn(),
    capNewest: vi.fn(),
  }
  return useChatPaging({
    topic: () => ({ id: 't1' }) as unknown as Topic,
    focusBlock: () => null,
    timeline: timeline as unknown as ReturnType<typeof useTimeline>,
    scrollRef: ref(scroller) as unknown as Ref<HTMLElement | null>,
    atBottom: ref(false),
    loadingHistory: ref(false),
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
    // 没动 scrollHeight（页面为空）：差是 0，scrollTop 不动。
    const timeline = {
      messages: ref([]),
      hasMore: ref(true),
      hasNewer: ref(false),
      oldestLoaded: () => 'cursor',
      newest: () => ({ blocks: [], hasMore: false }),
      find: () => undefined,
      showMiddle: vi.fn(),
      backToNewest: vi.fn(),
      prepend: vi.fn(),
      appendNewer: vi.fn(),
      capNewest: vi.fn(),
    }
    mocks.listBlocks.mockResolvedValue({ data: [], has_more: false })
    const paging = useChatPaging({
      topic: () => ({ id: 't1' }) as unknown as Topic,
      focusBlock: () => null,
      timeline: timeline as unknown as ReturnType<typeof useTimeline>,
      scrollRef: ref(scroller) as unknown as Ref<HTMLElement | null>,
      atBottom: ref(false),
      loadingHistory: ref(false),
      unseen: ref([]),
      errorMsg: ref(null),
      rememberScroll: () => {},
      scrollToMessage: () => {},
      scrollToBottom: () => {},
    })

    await paging.loadOlder()

    expect(scroller.scrollTop).toBe(120)
    frames.shift()?.(0)
    frames.shift()?.(0)
    expect(scroller.classes.has(MEASURE_CLASS)).toBe(false)
  })
})
