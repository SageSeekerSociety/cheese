/** 鼠标停在一行上时的预取。
 *
 * 预取是顺手做的事，所以这里的每一条都是关于「它什么时候**不**做」的：路过不算
 * 意图、取过就不再取、省流量和触屏一概让开、永远排在用户的请求后面、失败没有人
 * 看得见。最要命的一条在最后——预取绝不能顺手把话题标成已读，那个回归的表现是
 * 未读红点自己消失，而没有人会把它和「鼠标划过侧栏」联系起来。
 */
import type { BlockPage } from '@/api'

import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

vi.mock('@/api', () => ({
  archiveTopic: vi.fn(),
  createTopic: vi.fn(),
  getPrivateUnread: vi.fn().mockResolvedValue({}),
  getTopic: vi.fn(),
  getTopicUnread: vi.fn().mockResolvedValue({}),
  listBlocks: vi.fn(),
  listProjectMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listProjects: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listTopics: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  markTopicRead: vi.fn().mockResolvedValue(undefined),
  setTopicTitle: vi.fn(),
  unarchiveTopic: vi.fn(),
  upgradeBlock: vi.fn(),
}))

function page(ids: string[]): BlockPage {
  return {
    data: ids.map(
      (id) => ({ id, topic_id: 't', kind: 'message', content: id }) as unknown as BlockPage['data'][number]
    ),
    total: ids.length,
    has_more: false,
    oldest_id: ids[0] ?? null,
  }
}

function deferred<T>() {
  let settle!: (v: T) => void
  const promise = new Promise<T>((res) => {
    settle = res
  })
  promise.catch(() => {})
  return { promise, settle }
}

/** 一台带鼠标的机器，网络正常。 */
function desktop() {
  pointer('(hover: hover) and (pointer: fine)')
  Reflect.deleteProperty(navigator, 'connection')
}

/** 只有 `matched` 里写着的那种指针存在。 */
function pointer(matches: string) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({ matches: query === matches, media: query }),
  })
}

function connection(value: { saveData?: boolean; effectiveType?: string } | undefined) {
  if (value === undefined) Reflect.deleteProperty(navigator, 'connection')
  else Object.defineProperty(navigator, 'connection', { configurable: true, value })
}

/** 一个懒加载路由：component 就是那个 `() => import(...)` 函数。 */
function lazyRouter() {
  const load = vi.fn(async () => ({ default: { template: '<div />' } }))
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: load },
      { path: '/t/:id', name: 'topic', component: load },
      { path: '/p/:id', name: 'project', component: load },
    ],
  })
  return { router, load }
}

/** 每个用例一份全新的模块状态：「已经取过」的记忆不该跨用例传染。 */
async function fresh() {
  vi.resetModules()
  const api = await import('@/api')
  const prefetch = await import('./routePrefetch')
  const cache = await import('./blockCache')
  return {
    listBlocks: vi.mocked(api.listBlocks),
    markTopicRead: vi.mocked(api.markTopicRead),
    ...prefetch,
    refreshBlockCache: cache.refreshBlockCache,
    cachedWindow: cache.cachedWindow,
  }
}

/** 指针停住的那一刻。 */
async function pointerRests(ms = 200) {
  await vi.advanceTimersByTimeAsync(ms)
}

describe('hover 预取', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    setActivePinia(createPinia())
    desktop()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('指针刚进来还不算意图，什么都不发', async () => {
    const { prefetchOnHover, listBlocks } = await fresh()
    const { router, load } = lazyRouter()

    prefetchOnHover({ router, to: { name: 'topic', params: { id: 't1' } }, topicId: 't1' })
    await vi.advanceTimersByTimeAsync(50)

    expect(listBlocks).not.toHaveBeenCalled()
    expect(load).not.toHaveBeenCalled()
  })

  it('指针停住之后才去取', async () => {
    const { prefetchOnHover, listBlocks } = await fresh()
    listBlocks.mockResolvedValue(page(['b1']))
    const { router, load } = lazyRouter()

    prefetchOnHover({ router, to: { name: 'topic', params: { id: 't1' } }, topicId: 't1' })
    await pointerRests()

    expect(load).toHaveBeenCalled()
    expect(listBlocks).toHaveBeenCalledWith('t1', expect.anything())
  })

  it('停住之前指针就走了，一个请求都不发', async () => {
    const { prefetchOnHover, cancelPrefetch, listBlocks } = await fresh()
    listBlocks.mockResolvedValue(page(['b1']))
    const { router, load } = lazyRouter()

    prefetchOnHover({ router, to: { name: 'topic', params: { id: 't1' } }, topicId: 't1' })
    await vi.advanceTimersByTimeAsync(80)
    cancelPrefetch()
    await pointerRests(5000)

    expect(listBlocks).not.toHaveBeenCalled()
    expect(load).not.toHaveBeenCalled()
  })

  it('路过一整列话题只会预取最后停住的那一个', async () => {
    const { prefetchOnHover, listBlocks } = await fresh()
    listBlocks.mockResolvedValue(page(['b1']))
    const { router } = lazyRouter()

    for (const id of ['t1', 't2', 't3', 't4']) {
      prefetchOnHover({ router, to: { name: 'topic', params: { id } }, topicId: id })
      await vi.advanceTimersByTimeAsync(30)
    }
    await pointerRests()

    expect(listBlocks).toHaveBeenCalledTimes(1)
    expect(listBlocks).toHaveBeenCalledWith('t4', expect.anything())
  })

  it('同一个话题预取成功之后不再重复预取', async () => {
    const { prefetchOnHover, listBlocks } = await fresh()
    listBlocks.mockResolvedValue(page(['b1']))
    const { router } = lazyRouter()
    const hover = () => prefetchOnHover({ router, to: { name: 'topic', params: { id: 't1' } }, topicId: 't1' })

    hover()
    await pointerRests()
    hover()
    await pointerRests()

    expect(listBlocks).toHaveBeenCalledTimes(1)
  })

  it('同一个页面的代码只下一次', async () => {
    const { prefetchOnHover } = await fresh()
    const { router, load } = lazyRouter()

    prefetchOnHover({ router, to: { name: 'project', params: { id: 'p1' } } })
    await pointerRests()
    prefetchOnHover({ router, to: { name: 'project', params: { id: 'p1' } } })
    await pointerRests()

    expect(load).toHaveBeenCalledTimes(1)
  })

  it('去不到的地方不会把预取搞崩', async () => {
    const { prefetchOnHover } = await fresh()
    const { router, load } = lazyRouter()

    prefetchOnHover({ router, to: { name: 'nope', params: {} } })
    await pointerRests()

    expect(load).not.toHaveBeenCalled()
  })

  it('省流量模式下不预取', async () => {
    const { prefetchOnHover, listBlocks } = await fresh()
    listBlocks.mockResolvedValue(page(['b1']))
    const { router, load } = lazyRouter()
    connection({ saveData: true, effectiveType: '4g' })

    prefetchOnHover({ router, to: { name: 'topic', params: { id: 't1' } }, topicId: 't1' })
    await pointerRests()

    expect(listBlocks).not.toHaveBeenCalled()
    expect(load).not.toHaveBeenCalled()
  })

  it.each(['slow-2g', '2g'])('慢速网络(%s)下不预取', async (effectiveType) => {
    const { prefetchOnHover, listBlocks } = await fresh()
    listBlocks.mockResolvedValue(page(['b1']))
    const { router } = lazyRouter()
    connection({ effectiveType })

    prefetchOnHover({ router, to: { name: 'topic', params: { id: 't1' } }, topicId: 't1' })
    await pointerRests()

    expect(listBlocks).not.toHaveBeenCalled()
  })

  it('浏览器不给网络信息时照常预取', async () => {
    const { prefetchOnHover, listBlocks } = await fresh()
    listBlocks.mockResolvedValue(page(['b1']))
    const { router } = lazyRouter()
    connection(undefined)

    prefetchOnHover({ router, to: { name: 'topic', params: { id: 't1' } }, topicId: 't1' })
    await pointerRests()

    expect(listBlocks).toHaveBeenCalledTimes(1)
  })

  it('触屏上手指滑过不预取', async () => {
    const { prefetchOnHover, listBlocks } = await fresh()
    listBlocks.mockResolvedValue(page(['b1']))
    const { router, load } = lazyRouter()
    pointer('(hover: none) and (pointer: coarse)') // 手指，不是鼠标

    for (const id of ['t1', 't2', 't3']) {
      prefetchOnHover({ router, to: { name: 'topic', params: { id } }, topicId: id })
      await pointerRests()
    }

    expect(listBlocks).not.toHaveBeenCalled()
    expect(load).not.toHaveBeenCalled()
  })

  it('预取排在后台刷新的同一条闸后面，不另开一条', async () => {
    const { prefetchOnHover, refreshBlockCache, listBlocks } = await fresh()
    const held = deferred<BlockPage>()
    listBlocks.mockReturnValue(held.promise)
    const { router } = lazyRouter()

    // 两条闸道先被后台刷新占满
    void refreshBlockCache('busy-1')
    void refreshBlockCache('busy-2')
    await vi.advanceTimersByTimeAsync(0)
    expect(listBlocks).toHaveBeenCalledTimes(2)

    prefetchOnHover({ router, to: { name: 'topic', params: { id: 't9' } }, topicId: 't9' })
    await pointerRests()

    // 预取排队等着，没有把在飞的请求变成三个
    expect(listBlocks).toHaveBeenCalledTimes(2)

    held.settle(page(['b1']))
    await vi.advanceTimersByTimeAsync(0)
    expect(listBlocks).toHaveBeenLastCalledWith('t9', expect.anything())
  })

  it('预取失败不留任何痕迹', async () => {
    const { prefetchOnHover, listBlocks, cachedWindow } = await fresh()
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    listBlocks.mockRejectedValue(new Error('offline'))
    const { router } = lazyRouter()

    prefetchOnHover({ router, to: { name: 'topic', params: { id: 't1' } }, topicId: 't1' })
    await pointerRests()

    expect(cachedWindow('t1')).toBeNull()
    expect(error).not.toHaveBeenCalled()
    error.mockRestore()
  })

  it('预取只写缓存，不把话题标成已读', async () => {
    const { prefetchOnHover, listBlocks, markTopicRead, cachedWindow } = await fresh()
    listBlocks.mockResolvedValue(page(['b1']))
    const { router } = lazyRouter()
    const { useWorkspaceStore } = await import('@/stores/workspace')
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    store.unreadMap = { t1: 3 }

    prefetchOnHover({ router, to: { name: 'topic', params: { id: 't1' } }, topicId: 't1' })
    await pointerRests()

    expect(cachedWindow('t1')?.blocks).toHaveLength(1) // 确实取回来了
    expect(markTopicRead).not.toHaveBeenCalled() // 但没有动读游标
    expect(store.unreadMap).toEqual({ t1: 3 }) // 红点还在
  })
})
