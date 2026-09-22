/**
 * 本地那两份「关于上一条的记忆」——它们各有一个会**静默**出错的方向。
 *
 *  1. **已读的账本**（`markedReadIds`）：自动那条已读靠它去重。它错在两边——
 *     拿它当人手那一下的实现，会让「按了 `M` 没反应」；而它跨身份留着，会让
 *     下一个人点开同一条时那次自动已读被静默跳过。
 *  2. **详情显示的是哪一条**（`detailId` + `detail`）：作废如果只清 `detail` 而
 *     不把 `detailId` 一起前移，上一条迟到的响应就会被当成这一条收下，刚清掉的
 *     旧正文又贴回来。
 *
 * 两份都是**没有渲染出来的状态**，所以没有任何一条调用路径的报错提示它们坏了：
 * 屏幕上只是「不变」和「贴回旧内容」，两件都看着像没按对。所以这里逐条钉死。
 *
 * 队列页那一层的调用点（防抖、代次）在 `views/admin/AdminQueuePage.spec.ts` 里，
 * 这里只钉 store 这一侧。
 */
import type { FeedbackDetail } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAdminFeedback = vi.fn()
const getFeedbackCounts = vi.fn()
const markFeedbackRead = vi.fn()
const listAdminFeedback = vi.fn()
const setAdminFeedbackStatus = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAdminFeedback: (...a: unknown[]) => getAdminFeedback(...a),
    getFeedbackCounts: (...a: unknown[]) => getFeedbackCounts(...a),
    markFeedbackRead: (...a: unknown[]) => markFeedbackRead(...a),
    listAdminFeedback: (...a: unknown[]) => listAdminFeedback(...a),
    setAdminFeedbackStatus: (...a: unknown[]) => setAdminFeedbackStatus(...a),
  }
})

import { resetFeedbackCaches, useFeedbackStore } from '@/stores/feedback'

function detail(id: string, status = 'in_progress'): FeedbackDetail {
  return { id, status, title: `反馈 ${id}`, supports: 0, comments: 0 } as unknown as FeedbackDetail
}

/** 一次永远不结束的请求，用来把「响应还没回来」那一帧冻住。 */
function pending<T>(): { promise: Promise<T>; finish: (v: T) => void } {
  let finish: (v: T) => void = () => {}
  const promise = new Promise<T>((resolve) => {
    finish = resolve
  })
  return { promise, finish }
}

/** 等两个微任务：`.then()` 链跑完一轮就够，不需要真等待时间。 */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  setActivePinia(createPinia())
  // 两份记忆都是**模块级**的，跨用例活着的：不清的话，「账本里有没有 fb-1」由上一个
  // 用例决定，这一份文件里的用例就会互相牵着手过或不过（同 `feedback.cache.spec.ts`
  // 开头那条说明）。`resetFeedbackCaches()` 也是生产里换身份走的那条路，所以这里用
  // 它而不是别的私有办法。
  resetFeedbackCaches()
  getAdminFeedback.mockReset()
  getFeedbackCounts.mockReset()
  markFeedbackRead.mockReset()
  listAdminFeedback.mockReset()
  setAdminFeedbackStatus.mockReset()
  getFeedbackCounts.mockResolvedValue({ all: 0, hot: 0, active: 0, resolved: 0, unread: 0 })
  markFeedbackRead.mockResolvedValue({ last_read_at: '2026-09-21T00:00:00Z' })
  // 管理端每一次写都会顺带回一次列表（`_adminWrite`）。这条用例关心的是详情那一份，
  // 列表给一个空页就够，免得它去够真网络、把 `store.error` 点着。
  listAdminFeedback.mockResolvedValue({
    data: [],
    total: 0,
    counts: { all: 0, hot: 0, active: 0, resolved: 0, unread: 0 },
  })
})

describe('已读：人按的那一下与服务端那个游标', () => {
  it('自动已读记过的 id，人手再按一次也照样真发请求', async () => {
    const store = useFeedbackStore()
    getAdminFeedback.mockResolvedValue(detail('fb-1'))
    await store.loadAdminDetail('fb-1')

    // 打开这条 800ms 之后，页面自己推过一次（账本里从此有 fb-1）。
    await store.markReadOnce('fb-1')
    expect(markFeedbackRead).toHaveBeenCalledTimes(1)

    // 之后又攒出了新的未读，人按下 `M` / 页头那个按钮。
    store.counts = { ...store.counts, unread: 4 }
    await store.markRead()

    // 拿不动的表现是这里**还是 1**：请求没发、未读数不减、也没有任何提示。
    expect(markFeedbackRead).toHaveBeenCalledTimes(2)
    expect(store.counts.unread).toBe(0)
  })

  it('人手那一下成功之后，这一条的自动已读不再重复发一次', async () => {
    const store = useFeedbackStore()
    getAdminFeedback.mockResolvedValue(detail('fb-1'))
    await store.loadAdminDetail('fb-1')

    await store.markRead()
    expect(markFeedbackRead).toHaveBeenCalledTimes(1)

    // 页面那支 800ms 的定时器到点了 —— 它要推的是同一条，没有第二次的意义。
    await store.markReadOnce('fb-1')
    expect(markFeedbackRead).toHaveBeenCalledTimes(1)
  })

  it('人手那一下失败时不留账：自动那条还得能再试一次', async () => {
    const store = useFeedbackStore()
    getAdminFeedback.mockResolvedValue(detail('fb-1'))
    await store.loadAdminDetail('fb-1')

    markFeedbackRead.mockRejectedValueOnce(new Error('boom'))
    await store.markRead()

    // 游标其实没推成，这一条在服务端仍然是未读。账本上记了它，就再也清不掉了。
    await store.markReadOnce('fb-1')
    expect(markFeedbackRead).toHaveBeenCalledTimes(2)
  })

  it('换个人之后账本跟着清掉', async () => {
    const store = useFeedbackStore()
    await store.markReadOnce('fb-1')
    expect(markFeedbackRead).toHaveBeenCalledTimes(1)

    // 退出登录、或另一个人在这台机器上登进来。
    resetFeedbackCaches()
    setActivePinia(createPinia())

    await useFeedbackStore().markReadOnce('fb-1')
    expect(markFeedbackRead).toHaveBeenCalledTimes(2)
  })
})

describe('详情：光标挪走之后，旧的那一份不算数', () => {
  it('作废是「立刻」的，不等那 150ms 防抖', async () => {
    const store = useFeedbackStore()
    getAdminFeedback.mockResolvedValue(detail('fb-1'))
    await store.loadAdminDetail('fb-1')
    expect(store.detail?.id).toBe('fb-1')

    store.invalidateDetail('fb-2')

    expect(store.detail).toBeNull()
    expect(store.detailId).toBe('fb-2')
    expect(store.detailLoading).toBe(true)
    expect(store.error).toBeNull()
  })

  it('列出被筛空时作废，而且不会停在「正在拉」上', async () => {
    const store = useFeedbackStore()
    getAdminFeedback.mockResolvedValue(detail('fb-1'))
    await store.loadAdminDetail('fb-1')

    store.invalidateDetail(null)

    expect(store.detail).toBeNull()
    expect(store.detailId).toBeNull()
    expect(store.detailLoading).toBe(false)
  })

  it('作废之后，上一条迟到的响应贴不回来', async () => {
    const store = useFeedbackStore()

    // 上一条的请求还在飞……
    const first = pending<FeedbackDetail>()
    getAdminFeedback.mockImplementation(() => first.promise)
    void store.loadAdminDetail('fb-1')

    // ……这段时间里光标挪到了另一条（详情区当场作废）。
    store.invalidateDetail('fb-2')

    // 上一条的响应现在才回来。
    first.finish(detail('fb-1'))
    await settle()

    // 只清 `detail` 而不把 `detailId` 一起前移，这里就会画回 fb-1 的正文，
    // 而左边选中的行、头上的编号已经是 fb-2 了。
    expect(store.detail).toBeNull()
    expect(store.detailId).toBe('fb-2')
  })

  it('写完之后，写之前发出去的那次读不许把结果盖回去', async () => {
    const store = useFeedbackStore()

    // 打开这一条，那次读还在飞……（第一次跑 e2e 就是在这种交错里红的：面板上写回
    // 「已收录」，而库里已经是「处理中」。）
    const read = pending<FeedbackDetail>()
    getAdminFeedback.mockImplementationOnce(() => read.promise)
    void store.loadAdminDetail('fb-1')

    // ……这段时间里管理员按了「开始处理」，服务端回的详情是处理中，当场画上。
    setAdminFeedbackStatus.mockResolvedValueOnce(detail('fb-1', 'in_progress'))
    await store.setStatus('fb-1', 'in_progress')
    expect(store.detail?.status).toBe('in_progress')

    // 那次读现在才回来，带的是**写之前那一刻**的旧状态。
    read.finish(detail('fb-1', 'received'))
    await settle()

    // 读和写落在同一个 id 上，`detailId` 那条判据在这儿一句话都说不上：只有代次能
    // 分出先后。少了它，屏幕上会退回「已收录」——管理员看着像这一按没生效，再按一次。
    expect(store.detail?.status).toBe('in_progress')
    expect(store.detailLoading).toBe(false)
  })
})
