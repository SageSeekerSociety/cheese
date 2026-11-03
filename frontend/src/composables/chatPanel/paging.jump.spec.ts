/** 跳到某一条要在**滚动发生之前**声明「这一下是我们自己发的」。
 *
 * 只测 useChatScroll 的叶子不够：书签浮条 → `openAt` → `scrollToMessage` 这条线上，
 * 漏掉那一声声明，停在最底下的人点书签就会被改写兜底当成浏览器锚定、拽回底部
 * （真人 2026-10-26 反馈）。所以这条用例挂在调用点上：先声明，再滚。 */
import type { Ref } from 'vue'
import type { Topic } from '../../cx_types'

import { nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ listBlocks: vi.fn() }))
vi.mock('../../api', () => ({
  ApiError: class ApiError extends Error {},
  listBlocks: mocks.listBlocks,
}))

import { usePanelPaging } from './paging'

function setUp() {
  const row = document.createElement('div')
  row.scrollIntoView = vi.fn()
  const pane = document.createElement('div')
  pane.querySelector = (sel: string) => (sel === '[data-mid="m1"]' ? row : null)

  const beginOwnScroll = vi.fn()
  const window_ = {
    messages: ref([]),
    hasMore: ref(false),
    hasNewer: ref(false),
    oldestLoaded: () => 'cursor',
    newest: () => ({ blocks: [], hasMore: false }),
    find: (id: string) => (id === 'm1' ? { id } : undefined),
    showMiddle: vi.fn(),
    backToNewest: vi.fn(),
    prepend: vi.fn(),
    appendNewer: vi.fn(),
    capNewest: vi.fn(),
  }
  const paging = usePanelPaging({
    place: () => ({ id: 't1' }) as unknown as Topic,
    focusBlock: () => null,
    timeline: window_ as never,
    history: {} as never,
    tail: {} as never,
    runRecords: {} as never,
    rowBatch: { deferUntilRevealed: () => false, pending: ref(false), hidden: ref([]) } as never,
    scrollRef: ref(pane) as unknown as Ref<HTMLElement | null>,
    atBottom: ref(true),
    autoScroll: () => {},
    scrollToBottom: () => {},
    rememberScroll: () => {},
    forgetScroll: () => {},
    beginOwnScroll,
    loadingHistory: ref(false),
    unseen: ref([]),
    errorMsg: ref(null),
    settleOutbox: () => {},
    connectSocket: () => {},
    closeSocket: () => {},
    connectRefused: ref(false),
    failed: () => {},
    flash: () => {},
  })
  return { paging, row, beginOwnScroll }
}

afterEach(() => {
  mocks.listBlocks.mockReset()
})

describe('跳到某一条', () => {
  it('先声明这一下滚动是我们自己发的，再滚', async () => {
    const { paging, row, beginOwnScroll } = setUp()
    await paging.paging.openAt('m1')
    await nextTick()
    expect(row.scrollIntoView).toHaveBeenCalledWith(expect.objectContaining({ block: 'center' }))
    expect(beginOwnScroll).toHaveBeenCalledTimes(1)
    expect(beginOwnScroll.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(row.scrollIntoView).mock.invocationCallOrder[0]
    )
  })

  it('要跳的那一条不在这一窗里：还没滚，就先别声明', async () => {
    const { paging, row, beginOwnScroll } = setUp()
    // 不在窗口里时 `openAt` 会去读它前后的一段；这一读没成，就不该有滚动、也不该
    // 留一个「我们自己发的滚动」在飞。
    mocks.listBlocks.mockRejectedValue(new Error('nope'))
    await paging.paging.openAt('missing')
    expect(beginOwnScroll).not.toHaveBeenCalled()
    expect(row.scrollIntoView).not.toHaveBeenCalled()
  })
})
