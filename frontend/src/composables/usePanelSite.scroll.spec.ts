/**
 * 「现场」那一格停在哪：打开停在最新的一条；读者往上翻之后，3 秒一次的刷新和
 * socket 上来的一行都不能把他拽回底部；他自己滚回底部之后，新的行接着跟。
 *
 * jsdom 不做布局，所以这里用一个会记账的假滚动容器代替 `.panel-site`：scrollHeight /
 * clientHeight 由测试按盒子给，scrollTop 的赋值照浏览器那样夹在可滚范围内。
 */
import type { Block } from '../cx_types'

import { effectScope, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getTranscript: vi.fn(), sessionRecordImageUrl: vi.fn() }))
vi.mock('../api', () => ({ ...api, SITE_PAGE_SIZE: 120 }))
vi.mock('./useSessionInspector', () => ({ useSessionInspector: () => ({}) }))
vi.mock('@/i18n', () => ({ t: (key: string) => key }))

import { storageRemove } from '../lib/browserStorage'
import { cachedWindow, forgetWindows, rememberWindow } from '../lib/siteWindow'

import { usePanelSite } from './usePanelSite'

beforeEach(() => {
  // 现场那一窗存在模块级的表里（`lib/siteWindow`）：前一条用例留下的窗口会变成
  // 后一条的「上次打开」。清掉，每条用例都从没有缓存开始。
  forgetWindows()
  vi.useFakeTimers()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  )
  // 钉底排的是下一个动画帧：测试里当场执行，和这一拍落在一起。
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    cb(0)
    return 0
  })
  storageRemove('cheese.site.view')
  api.getTranscript.mockReset()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function pane(box: { scrollHeight: number; clientHeight: number; scrollTop: number }) {
  const el = document.createElement('div')
  Object.defineProperty(el, 'scrollHeight', { get: () => box.scrollHeight })
  Object.defineProperty(el, 'clientHeight', { get: () => box.clientHeight })
  Object.defineProperty(el, 'scrollTop', {
    get: () => box.scrollTop,
    set: (v: number) => (box.scrollTop = Math.max(0, Math.min(v, box.scrollHeight - box.clientHeight))),
  })
  return el
}

function block(id: string, second: number): Block {
  return {
    id,
    conversation_id: 'room',
    kind: 'event',
    author_type: 'participant',
    author: 'cheese-a',
    content: '',
    created_at: `2026-10-19T10:00:${String(second).padStart(2, '0')}Z`,
    refs: [],
    meta: { record: { type: 'assistant', message: { content: [] } }, parts: [], tool: 'Bash' },
  }
}

function row(id: string, second: number): Block {
  return { ...block(id, second), run_record: false } as Block
}

function page(rows: Block[], extra: { has_more?: boolean; oldest_id?: string | null; newest_id?: string | null } = {}) {
  return {
    data: rows,
    has_more: extra.has_more ?? false,
    oldest_id: extra.oldest_id ?? null,
    newest_id: extra.newest_id ?? null,
  }
}

const OPEN = page([block('a', 1), block('b', 2)])

async function settle(): Promise<void> {
  for (let i = 0; i < 4; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

async function openAtBottom(topic: { id: string } = { id: 'room' }) {
  const box = { scrollHeight: 2000, clientHeight: 500, scrollTop: 0 }
  const el = pane(box)
  const scope = effectScope()
  const site = scope.run(() =>
    usePanelSite({
      topicId: () => topic.id,
      active: () => true,
      refreshTick: () => 0,
      runningTurns: () => ({}),
      agentControl: () => null,
    })
  )!
  site.scrollRef.value = el
  await settle()
  return { box, el, site, scope }
}

describe('where 现场 opens', () => {
  it('lands on the newest record', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, scope } = await openAtBottom()
    expect(box.scrollTop).toBe(1500)
    scope.stop()
  })

  it('lands on the newest record of the teammate the reader switches to', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, el, site, scope } = await openAtBottom()

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))

    site.selectAgent('cheese-a')
    await settle()

    expect(box.scrollTop).toBe(1500)
    scope.stop()
  })
})

describe('a row the socket updates in place', () => {
  it('keeps its cursor, so next time can still page up from it', async () => {
    // 读回来的那一页每条都带游标，socket 的 `run_record` 帧不带。原地换掉时不能把
    // 游标弄丢：缓存要靠它定位，丢了下次打开就翻不动了。
    const withCursor = row('a', 1)
    withCursor.meta = { ...withCursor.meta, cursor: 'c-a' }
    api.getTranscript.mockResolvedValue(page([withCursor], { newest_id: 'n1' }))
    const { site, scope } = await openAtBottom()
    await settle()

    const updated = { ...row('a', 1), content: '后来的一句话' }
    site.receive(updated)
    await settle()
    // 缓存是下一次刷新时才写回去的：等它写一次，再看到底存了什么。
    vi.advanceTimersByTime(3000)
    await settle()

    const held = cachedWindow('room', null, 120)!
    expect(held.blocks[0].meta?.cursor, '原地换掉时游标留着').toBe('c-a')
    scope.stop()
  })
})

describe('switching to another conversation', () => {
  it("does not keep the previous conversation's records", async () => {
    // 换对话是就地换的（任务路由没有 topicId 这个 key）：手上那几行是上一条对话的，
    // 不能留在新的这一条里。这里让新对话的最新一页比旧的**更早**——`mergeSite` 那条
    // 「把更旧的接在前面」的分支正是这么漏出来的。
    const topic = { id: 'room' }
    api.getTranscript.mockResolvedValueOnce(page([row('a1', 5), row('a2', 6)]))
    const { site, scope } = await openAtBottom(topic)
    expect(site.transcript.value.map((b) => b.id)).toEqual(['a1', 'a2'])

    topic.id = 'task'
    api.getTranscript.mockResolvedValue(page([row('b1', 1), row('b2', 2)]))
    vi.advanceTimersByTime(3000)
    await settle()

    expect(
      site.transcript.value.map((b) => b.id),
      '只留下新对话的'
    ).toEqual(['b1', 'b2'])
    scope.stop()
  })
})

describe('the window from last time', () => {
  it('paints it at once, then asks only for what came after it', async () => {
    forgetWindows()
    // 上一次打开留下的那一窗（`lib/siteWindow`）。这一条用例前把它放进去。
    rememberWindow(
      'room',
      null,
      {
        blocks: [row('a', 1), row('b', 2)],
        hasMore: true,
        oldestCursor: 'c1',
        newestCursor: 'n1',
      },
      120
    )
    let answer!: (v: ReturnType<typeof page>) => void
    api.getTranscript.mockImplementationOnce(
      () =>
        new Promise((r) => {
          answer = r
        })
    )

    const box = { scrollHeight: 2000, clientHeight: 500, scrollTop: 0 }
    const el = pane(box)
    const scope = effectScope()
    const site = scope.run(() =>
      usePanelSite({
        topicId: () => 'room',
        active: () => true,
        refreshTick: () => 0,
        runningTurns: () => ({}),
        agentControl: () => null,
      })
    )!
    site.scrollRef.value = el
    await settle()

    // 请求还在路上，屏上已经是上次那一窗了——不是骨架屏。
    expect(site.loading.value, '有缓存就不摆骨架屏').toBe(false)
    expect(site.transcript.value.map((b) => b.id)).toEqual(['a', 'b'])
    // 问的是「从这一窗往后有什么」，不是重读一整页。
    expect(api.getTranscript.mock.calls[0][1]).toMatchObject({ after: 'n1' })

    answer(page([row('c', 3)], { newest_id: 'n2' }))
    await settle()
    // 新的接在末尾，不是把整窗换掉。
    expect(site.transcript.value.map((b) => b.id)).toEqual(['a', 'b', 'c'])
    // 往前读的这一路不说「上面还有没有」，所以它不该把缓存里的往上翻能力冲掉。
    expect(site.hasOlder.value, '缓存打开后仍能往上翻').toBe(true)
    scope.stop()
  })

  it('keeps the cursor when a refresh brings nothing new', async () => {
    forgetWindows()
    rememberWindow('room', null, { blocks: [row('a', 1)], hasMore: false, oldestCursor: null, newestCursor: 'n1' }, 120)
    // 追平了：这一页是空的，也没有新的游标可给。
    api.getTranscript.mockResolvedValue(page([], { newest_id: null }))
    const { site, scope } = await openAtBottom()
    await settle()

    api.getTranscript.mockClear()
    vi.advanceTimersByTime(3000)
    await settle()

    // 又一次从同一个游标往后读，没有退回整页重读。
    expect(api.getTranscript.mock.calls[0][1], '空的一页之后手上还有游标').toMatchObject({
      after: 'n1',
    })
    scope.stop()
  })

  it("does not paint another view's window", async () => {
    forgetWindows()
    rememberWindow(
      'room',
      'cheese-a',
      {
        blocks: [row('a', 1)],
        hasMore: false,
        oldestCursor: null,
        newestCursor: 'n1',
      },
      120
    )
    api.getTranscript.mockResolvedValue(page([row('b', 2)]))
    const { site, scope } = await openAtBottom()

    // 「全部」这一视角没有缓存，不该把「只看 cheese-a」那一窗端上来。
    expect(site.transcript.value.map((b) => b.id)).toEqual(['b'])
    scope.stop()
  })

  it('never shows the same record twice when paging up past the cursor it held', async () => {
    // 缓存的游标可能退到一条**更靠后**的行上（画出来的头一条是 socket 换上来的、
    // 不带游标的那种，见 lib/siteWindow）。从它往回读会把已经画出来的几条再读一遍：
    // 拼之前必须按 id 去重，不然同一个 id 在列表里出现两次。
    forgetWindows()
    const a = row('a', 1)
    a.meta = { ...a.meta, cursor: 'c-a' }
    const b = row('b', 2)
    b.meta = { ...b.meta, cursor: 'c-b' }
    rememberWindow('room', null, { blocks: [a, b], hasMore: true, oldestCursor: 'c-b', newestCursor: 'n1' }, 1)
    api.getTranscript.mockResolvedValue(page([a, b], { has_more: false }))
    const { site, scope } = await openAtBottom()
    await settle()

    site.onSiteScroll()
    await settle()

    const ids = site.transcript.value.map((x) => x.id)
    expect(ids.length, '同一个 id 不该出现两次').toBe(new Set(ids).size)
    scope.stop()
  })

  it('keeps the newest end when one page alone floods the window', async () => {
    // 一页就灌过封顶（700 条 > MAX_WINDOW），开着的读者停在末尾：留下的该是最新的 600
    // 条，裁掉的是更早的那一截。窗口的尾巴还连着最新的一条，下一次刷新接着往后读就行。
    forgetWindows()
    rememberWindow('room', null, { blocks: [row('a', 1)], hasMore: false, oldestCursor: null, newestCursor: 'n1' }, 120)
    const flood = Array.from({ length: 700 }, (_, i) => row(`f${i}`, 10 + i))
    api.getTranscript.mockResolvedValue(page(flood, { newest_id: 'n2' }))
    const { site, scope } = await openAtBottom()
    await settle()

    const ids = site.transcript.value.map((b) => b.id)
    expect(ids, '窗口封在 MAX_WINDOW').toHaveLength(600)
    expect(ids[ids.length - 1], '最新那一截留着').toBe('f699')
    expect(ids[0], '裁掉的是更早的那一截').toBe('f100')

    api.getTranscript.mockClear()
    vi.advanceTimersByTime(3000)
    await settle()
    expect(api.getTranscript.mock.calls[0][1], '窗口还连着最新的一条，接着往后读').toMatchObject({
      after: 'n2',
    })
    scope.stop()
  })
})

describe('the pages above the window', () => {
  it('prefetches older pages after the first one lands, without any scrolling', async () => {
    api.getTranscript
      .mockResolvedValueOnce(page([block('a', 1), block('b', 2)], { has_more: true, oldest_id: 'c1' }))
      .mockResolvedValue(page([block('z', 0)]))
    const { box, site, scope } = await openAtBottom()

    // The page above the first one came in without the reader asking for it, and
    // it is on screen above what they opened on.
    expect(site.transcript.value.map((b) => b.id)).toEqual(['z', 'a', 'b'])
    // 它取的是**更早**那一页：从第一页的最老一条往前读，不是重读最新一页。
    expect(api.getTranscript.mock.calls[1][1]).toMatchObject({ before: 'c1' })
    expect(site.loadingOlder.value, '预取是安静的，不摆「加载更早的现场…」').toBe(false)
    // 他在末尾，往上多出一页不该把他挪走。
    expect(box.scrollTop).toBe(1500)
    scope.stop()
  })

  it('keeps the records on screen when a prefetch fails', async () => {
    api.getTranscript
      .mockResolvedValueOnce(page([block('a', 1), block('b', 2)], { has_more: true, oldest_id: 'c1' }))
      .mockRejectedValue(new Error('请求等待超时，请重试'))
    const { site, scope } = await openAtBottom()

    // 读者没在等这一页：失败只是这次没提前拿到，不该把屏上的记录换成一条红条。
    expect(site.errorMsg.value).toBeNull()
    expect(site.transcript.value).toHaveLength(2)
    scope.stop()
  })

  it('tops the window back up when the reader reads a page at the edge', async () => {
    // 预取那一次失败了，屏上留着头一页。读者翻到顶自己读一页成功之后，后台该接着
    // 把再上面一页补出来——而不是等他再翻一次才补。
    api.getTranscript
      .mockResolvedValueOnce(page([block('a', 1), block('b', 2)], { has_more: true, oldest_id: 'c1' }))
      .mockRejectedValueOnce(new Error('请求等待超时，请重试'))
      .mockResolvedValueOnce(page([block('z', 0)], { has_more: true, oldest_id: 'c0' }))
      .mockResolvedValue(page([block('y', -1)], { has_more: false }))
    const { box, site, scope } = await openAtBottom()

    // 读者翻到顶：这是他自己的那一页（'z'），它上面还有一页（'y'）只有后台补得到。
    box.scrollTop = 0
    site.onSiteScroll()
    await settle()

    expect(site.transcript.value.map((b) => b.id)).toEqual(['y', 'z', 'a', 'b'])
    scope.stop()
  })
})

describe('a reader who scrolled up', () => {
  it('is not moved by the 3-second refresh', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, el, scope } = await openAtBottom()

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))

    api.getTranscript.mockClear()
    vi.advanceTimersByTime(3000)
    await settle()

    expect(api.getTranscript).toHaveBeenCalledTimes(1)
    expect(box.scrollTop).toBe(600)
    scope.stop()
  })

  it('is not moved by a refresh that was already in flight when they scrolled up', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, el, scope } = await openAtBottom()

    // The 3-second refresh starts while the reader is still at the end, then they
    // scroll up before the page comes back.
    let answer!: (value: ReturnType<typeof page>) => void
    api.getTranscript.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = resolve
        })
    )
    vi.advanceTimersByTime(3000)
    await Promise.resolve()

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))
    answer(OPEN)
    await settle()

    expect(box.scrollTop).toBe(600)
    scope.stop()
  })

  it('is not moved by a record arriving over the socket', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, el, site, scope } = await openAtBottom()

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))

    box.scrollHeight = 2400
    site.receive(row('c', 3))
    await settle()

    expect(box.scrollTop).toBe(600)
    scope.stop()
  })
})

describe('a reader back at the end', () => {
  it('follows a record arriving over the socket again', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, el, site, scope } = await openAtBottom()

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))
    box.scrollTop = 1500
    el.dispatchEvent(new Event('scroll'))

    box.scrollHeight = 2400
    site.receive(row('c', 3))
    await settle()

    expect(box.scrollTop).toBe(1900)
    scope.stop()
  })
})

describe('a read that is slow or fails', () => {
  it('keeps one read in flight and runs one more after it', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { scope } = await openAtBottom()

    // The backend is slow: the read has not come back when two more refreshes
    // and a socket line ask for one.
    api.getTranscript.mockClear()
    let answer!: (value: ReturnType<typeof page>) => void
    api.getTranscript.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = resolve
        })
    )
    vi.advanceTimersByTime(3000)
    await settle()
    expect(api.getTranscript).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(3000)
    vi.advanceTimersByTime(3000)
    await settle()
    expect(api.getTranscript).toHaveBeenCalledTimes(1)

    // It comes back: the waiting refreshes collapse into one more read, not three.
    answer(OPEN)
    await settle()
    expect(api.getTranscript).toHaveBeenCalledTimes(2)

    scope.stop()
  })

  it('keeps the records on screen when a background refresh fails', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { site, scope } = await openAtBottom()
    const rows = site.transcript.value.length

    api.getTranscript.mockRejectedValue(new Error('请求等待超时，请重试'))
    vi.advanceTimersByTime(3000)
    await settle()

    expect(site.errorMsg.value).toBeNull()
    expect(site.transcript.value).toHaveLength(rows)
    scope.stop()
  })

  it('shows the error when there is nothing on screen yet', async () => {
    api.getTranscript.mockRejectedValue(new Error('请求等待超时，请重试'))
    const { site, scope } = await openAtBottom()

    expect(site.errorMsg.value).toBe('请求等待超时，请重试')
    scope.stop()
  })

  it('retries the page the reader was paging up to, not the newest one', async () => {
    // 读者翻到顶、那一页失败：重试要重发**那一页**（带 `before` 游标），不是跳回
    // 末尾读最新一页。后者在他停在历史中间、窗口又被裁过时（`hasNewer`）会被守卫
    // 整个丢掉——一次请求都不发，那颗按钮按了等于没按。
    api.getTranscript.mockResolvedValueOnce(
      page([block('a', 1), block('b', 2)], { has_more: true, oldest_id: 'a', newest_id: 'b' })
    )
    const { box, el, site, scope } = await openAtBottom()

    api.getTranscript.mockClear()
    api.getTranscript.mockRejectedValueOnce(new Error('请求等待超时，请重试'))
    box.scrollTop = 0
    el.dispatchEvent(new Event('scroll'))
    site.onSiteScroll()
    await settle()
    expect(site.errorMsg.value, '往上翻的那一页失败了').toBe('请求等待超时，请重试')
    expect(
      api.getTranscript.mock.calls.filter((call) => call[1]?.before),
      '失败的那一次是往回读'
    ).toHaveLength(1)

    api.getTranscript.mockClear()
    api.getTranscript.mockResolvedValue(page([block('z', 0)], { has_more: false }))
    site.retry()
    await settle()

    const args = api.getTranscript.mock.calls.map((call) => call[1] ?? {})
    expect(args, '重试重发的是那一页').toEqual([{ limit: 120, before: 'a', author: null }])
    expect(site.errorMsg.value, '读回来了就把错误收掉').toBeNull()
    scope.stop()
  })

  it('drops the error the moment the retried read goes out, so the reader sees it work', async () => {
    // 错误态占着整块地方（`PanelSite` 里它是 `v-else-if`，记录和「加载更早的现场…」都
    // 在 `v-else`）。按下重试当场把错误收掉，读者立刻看见记录回来、那一句「加载更早的
    // 现场…」在转——而不是对着一条一动不动的红条等。
    api.getTranscript.mockResolvedValueOnce(
      page([block('a', 1), block('b', 2)], { has_more: true, oldest_id: 'a', newest_id: 'b' })
    )
    let n = 0
    api.getTranscript.mockImplementation(async () => {
      const k = n
      n += 1
      return page(
        Array.from({ length: 120 }, (_, i) => block(`p${k}_${i}`, i % 60)),
        { has_more: true, oldest_id: `p${k}_0` }
      )
    })
    const { box, site, scope } = await openAtBottom()
    await settle()

    // 读者往上翻：这一页失败。
    api.getTranscript.mockRejectedValueOnce(new Error('请求等待超时，请重试'))
    box.scrollTop = 0
    site.onSiteScroll()
    await settle()
    expect(site.errorMsg.value).toBe('请求等待超时，请重试')

    // 按下重试：请求挂在路上。错误这一刻就该收掉。
    let release!: () => void
    api.getTranscript.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve(page([block('z', 0)], { has_more: false }))
        })
    )
    site.retry()
    await settle()
    expect(site.errorMsg.value, '错误在请求发出去的那一刻就收掉').toBeNull()
    expect(site.loadingOlder.value, '屏上那一句「加载更早的现场…」要立起来').toBe(true)

    release()
    await settle()
    scope.stop()
  })

  it('does not let a successful page-up dismiss a newest-page failure', async () => {
    // `clearError()` 抹的是「此刻画着的那条错误」。往回读读成了，只该收「往上翻
    // 失败」那一条；此刻画着的如果是最新一页的失败（或一个 403），不能替它做主——
    // 那会把读者刚看到的那句原因和它那颗重试一起抹掉，而最新一页其实还没读到。
    //
    // 造法：打开「全部」，再切到某个队友——那是个有缓存的视角（`lib/siteWindow`），
    // 屏上先有那一窗（于是 `hasNewer` 不立、往上翻仍可行），而最新一页的读失败，
    // 错误就画在那一格上。
    api.getTranscript.mockResolvedValueOnce(
      page([block('a', 1), block('b', 2)], { has_more: true, oldest_id: 'a', newest_id: 'b' })
    )
    rememberWindow(
      'room',
      'cheese-a',
      {
        blocks: [block('x', 5), block('y', 6)],
        hasMore: true,
        oldestCursor: 'x',
        newestCursor: 'y',
      },
      120
    )
    const { box, el, site, scope } = await openAtBottom()
    await settle()

    // 切到那个队友：缓存画上去，最新一页的读失败。
    api.getTranscript.mockRejectedValueOnce(new Error('没有权限'))
    site.selectAgent('cheese-a')
    await settle()
    expect(site.errorMsg.value, '这一格报的是最新一页的失败').toBe('没有权限')

    // 读者往上翻：这一页读成了。
    api.getTranscript.mockImplementationOnce(async () => page([block('w', 4)], { has_more: false, oldest_id: 'w' }))
    box.scrollTop = 0
    el.dispatchEvent(new Event('scroll'))
    site.onSiteScroll()
    await settle()

    expect(site.errorMsg.value, '往回读读成了，不该替最新一页那条错误做主').toBe('没有权限')
    scope.stop()
  })
})

describe('a topic with no records yet', () => {
  it('does not flash the skeleton on every background refresh', async () => {
    api.getTranscript.mockResolvedValue(page([]))
    const { site, scope } = await openAtBottom()
    expect(site.transcript.value).toHaveLength(0)
    expect(site.loading.value).toBe(false)

    // 下一次刷新还在路上：屏上该一直停在那句空状态里，不该切回骨架屏。
    let answer!: (value: ReturnType<typeof page>) => void
    api.getTranscript.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = resolve
        })
    )
    vi.advanceTimersByTime(3000)
    await settle()
    expect(site.loading.value).toBe(false)

    answer(page([]))
    await settle()
    expect(site.loading.value).toBe(false)
    scope.stop()
  })
})

describe('the window at its cap', () => {
  // 读回来的一页每条都带着自己的游标（`topics_transcript` 给每条都编了一个），往回翻
  // 就从这一条接着读。时间按秒排开，跨过一分钟也不会撞在一起。
  function at(i: number): string {
    return new Date(Date.UTC(2026, 9, 19, 10, 0, 0) + i * 1000).toISOString()
  }
  function rec(i: number): Block {
    const one = row(`r${i}`, 0)
    return { ...one, created_at: at(i), meta: { ...one.meta, cursor: `c${i}` } }
  }

  it('keeps the newest end, so the reader never gets scrolled back', async () => {
    // 停在末尾的人看的是**最新**那一截。窗口涨过 MAX_WINDOW 时裁掉的只能是更早的
    // 那一头：裁最新那头的话，新来的行一进窗口就被裁走，他眼前的现场就停在几分钟前
    // 不动了（每三秒一次刷新能一直这么转下去）。
    let read = 0
    api.getTranscript.mockImplementation(async (_topic: string, args: { before?: string | null } = {}) => {
      if (args.before) return page([], { has_more: false }) // 上面没有了，别让预取搅进来
      const from = read * 120
      read += 1
      return page(
        Array.from({ length: 120 }, (_, k) => rec(from + k)),
        { newest_id: `c${from + 119}` }
      )
    })
    const { site, scope } = await openAtBottom()
    await settle()

    // 每三秒来一页新的，读者一直停在末尾（打开时就钉在那儿了）。
    for (let i = 0; i < 5; i += 1) {
      vi.advanceTimersByTime(3000)
      await settle()
    }
    // 7 页共 840 条，裁过两次：留下的该是最新的 600 条。
    const ids = site.transcript.value.map((b) => b.id)
    expect(ids, '窗口封在 MAX_WINDOW').toHaveLength(600)
    expect(ids[ids.length - 1], '最新那一条还在窗口里').toBe('r719')
    expect(ids[0], '裁掉的是更早的那一头').toBe('r120')

    // 尾巴还连着最新的一条：下一次刷新只问后来的那几条，不要整页重读。
    api.getTranscript.mockClear()
    vi.advanceTimersByTime(3000)
    await settle()
    expect(api.getTranscript.mock.calls[0][1]).toMatchObject({ after: 'c719' })
    scope.stop()
  })

  it('pages back up from the row it kept, so the end it cut is reachable', async () => {
    // 裁掉最旧那一截不算丢：那几条还在库里，往上翻的游标退到新头一条（c120）就还能
    // 读回来。游标要是没跟着挪（还指着已被裁掉的 r0），从它往回读会跳过被裁的那一段。
    let read = 0
    api.getTranscript.mockImplementation(async (_topic: string, args: { before?: string | null } = {}) => {
      if (args.before) {
        return page(
          Array.from({ length: 120 }, (_, k) => rec(k)),
          { has_more: false, oldest_id: 'c0' }
        )
      }
      const from = read * 120
      read += 1
      return page(
        Array.from({ length: 120 }, (_, k) => rec(from + k)),
        { newest_id: `c${from + 119}` }
      )
    })
    const { box, el, site, scope } = await openAtBottom()
    await settle()
    for (let i = 0; i < 5; i += 1) {
      vi.advanceTimersByTime(3000)
      await settle()
    }
    expect(site.transcript.value.map((b) => b.id)[0]).toBe('r120')

    // 读者翻到顶：读的是新头一条的游标，读回来的正是被裁掉的那 120 条。
    box.scrollTop = 0
    el.dispatchEvent(new Event('scroll'))
    site.onSiteScroll()
    await settle()

    expect(api.getTranscript.mock.calls.at(-1)?.[1], '从留下的头一条往回读').toMatchObject({
      before: 'c120',
    })
    const ids = site.transcript.value.map((b) => b.id)
    expect(ids.slice(0, 3), '被裁掉的那几条回来了').toEqual(['r0', 'r1', 'r2'])
    expect(ids.length, '同一个 id 不该出现两次').toBe(new Set(ids).size)
    scope.stop()
  })
})
