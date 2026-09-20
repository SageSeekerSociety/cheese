/** 时间线缓存的后台刷新：它必须永远排在用户后面。
 *
 * 这里钉的是「一次能发出去几个请求」这件事本身。刷新缓存是顺手做的事，没有人在
 * 等它；一旦它能同时发出去十几个，占满的就是后端那点数据库连接，被挤掉的会是用
 * 户此刻真正在等的那一个（线上表现是刷新页面必现的一串 500）。
 */
import type { BlockPage } from '@/api'

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api', () => ({ listBlocks: vi.fn() }))

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
  let fail!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    settle = res
    fail = rej
  })
  // 谁都可能不去 await 它，别让 node 把它当成没人管的 rejection
  promise.catch(() => {})
  return { promise, settle, fail }
}

/** 每个用例一份全新的模块状态：闸道计数和在飞表都不该跨用例传染。 */
async function fresh() {
  vi.resetModules()
  const api = await import('@/api')
  const cache = await import('./blockCache')
  return { listBlocks: vi.mocked(api.listBlocks), ...cache }
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('refreshBlockCache', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('同一个话题已经有一条在飞时，不再发第二条', async () => {
    const { listBlocks, refreshBlockCache } = await fresh()
    const first = deferred<BlockPage>()
    listBlocks.mockReturnValue(first.promise)

    const a = refreshBlockCache('t1')
    const b = refreshBlockCache('t1')
    await flush()

    expect(listBlocks).toHaveBeenCalledTimes(1)

    first.settle(page(['b1']))
    expect(await a).toEqual(await b)
  })

  it('在飞的那条落地之后，同一个话题可以再刷一次', async () => {
    const { listBlocks, refreshBlockCache } = await fresh()
    listBlocks.mockResolvedValue(page(['b1']))

    await refreshBlockCache('t1')
    await refreshBlockCache('t1')

    expect(listBlocks).toHaveBeenCalledTimes(2)
  })

  it('一次最多两个请求在飞，其余排队', async () => {
    const { listBlocks, refreshBlockCache } = await fresh()
    const held = deferred<BlockPage>()
    listBlocks.mockReturnValue(held.promise)

    void refreshBlockCache('t1')
    void refreshBlockCache('t2')
    void refreshBlockCache('t3')
    await flush()

    expect(listBlocks).toHaveBeenCalledTimes(2)
  })

  it('一条落地之后，排队的那个才出发', async () => {
    const { listBlocks, refreshBlockCache } = await fresh()
    const held = [deferred<BlockPage>(), deferred<BlockPage>(), deferred<BlockPage>()]
    let n = 0
    listBlocks.mockImplementation(() => held[n++]!.promise)

    void refreshBlockCache('t1')
    void refreshBlockCache('t2')
    void refreshBlockCache('t3')
    await flush()
    expect(listBlocks).toHaveBeenCalledTimes(2)

    held[0]!.settle(page(['b1']))
    await flush()

    expect(listBlocks).toHaveBeenCalledTimes(3)
    expect(listBlocks).toHaveBeenLastCalledWith('t3', expect.anything())
  })

  it('请求失败时不抛出，缓存保持原样', async () => {
    const { listBlocks, refreshBlockCache, cachedWindow, setCachedWindow } = await fresh()
    setCachedWindow('t1', { blocks: page(['old']).data, hasMore: false })
    listBlocks.mockRejectedValue(new Error('offline'))

    await expect(refreshBlockCache('t1')).resolves.toBeNull()
    expect(cachedWindow('t1')?.blocks.map((b) => b.id)).toEqual(['old'])
  })

  it('失败也会让出闸道，不会把队列堵死', async () => {
    const { listBlocks, refreshBlockCache } = await fresh()
    listBlocks.mockRejectedValue(new Error('offline'))

    await Promise.all([refreshBlockCache('t1'), refreshBlockCache('t2'), refreshBlockCache('t3')])
    await flush()

    expect(listBlocks).toHaveBeenCalledTimes(3)
  })
})
