// 房间最新那一段对话的缓存：打开房间、预取、后台刷新都经过它。
import type { Block } from '@/cx_types'

import { afterEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()
vi.mock('@/api', () => ({ listBlocks: (...args: unknown[]) => listBlocks(...args) }))

const { cachedWindow, prefetchNewestBlocks, readNewestBlocks, setCachedWindow } = await import('@/query/blocks')

function block(id: string): Block {
  return { id, conversation_id: 'room', kind: 'message', content: id, created_at: '2026-10-09T00:00:00Z' } as Block
}

function page(ids: string[], hasMore = false) {
  return { data: ids.map(block), has_more: hasMore, oldest_id: ids[0] ?? null, has_newer: false, newest_id: null }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => (resolve = res))
  return { promise, resolve }
}

afterEach(() => {
  listBlocks.mockReset()
})

describe('打开房间', () => {
  it('导航已经起头的那次读还在路上时，房间自己不另发一次', async () => {
    const answer = deferred<ReturnType<typeof page>>()
    listBlocks.mockReturnValue(answer.promise)
    const prefetched = prefetchNewestBlocks('room')
    const opened = readNewestBlocks('room')
    answer.resolve(page(['a', 'b']))
    await prefetched
    expect((await opened).blocks.map((b) => b.id)).toEqual(['a', 'b'])
    expect(listBlocks).toHaveBeenCalledTimes(1)
  })

  it('后台刷新不丢掉已经往上翻出来的历史', async () => {
    setCachedWindow('room', { blocks: ['old', 'a', 'b'].map(block), hasMore: true })
    listBlocks.mockResolvedValue(page(['a', 'b', 'c']))
    const window = await readNewestBlocks('room')
    expect(window.blocks.map((b) => b.id)).toEqual(['old', 'a', 'b', 'c'])
    expect(cachedWindow('room')?.blocks.map((b) => b.id)).toEqual(['old', 'a', 'b', 'c'])
  })
})

describe('后台预取', () => {
  it('同一时刻最多两个请求在路上，其余排队，一个落地才放下一个', async () => {
    const answers = new Map<string, ReturnType<typeof deferred<ReturnType<typeof page>>>>()
    listBlocks.mockImplementation((room: string) => {
      const answer = deferred<ReturnType<typeof page>>()
      answers.set(room, answer)
      return answer.promise
    })
    const runs = ['r1', 'r2', 'r3', 'r4'].map((room) => prefetchNewestBlocks(room))
    await vi.waitFor(() => expect(listBlocks).toHaveBeenCalledTimes(2))
    answers.get('r1')!.resolve(page(['x']))
    await vi.waitFor(() => expect(listBlocks).toHaveBeenCalledTimes(3))
    answers.get('r2')!.resolve(page(['x']))
    answers.get('r3')?.resolve(page(['x']))
    await vi.waitFor(() => expect(listBlocks).toHaveBeenCalledTimes(4))
    answers.get('r4')!.resolve(page(['x']))
    await Promise.all(runs)
  })

  it('没读成也让出位置，后面排着的照样出发', async () => {
    listBlocks.mockRejectedValueOnce(new Error('down')).mockRejectedValueOnce(new Error('down'))
    listBlocks.mockResolvedValue(page(['x']))
    await Promise.all(['r1', 'r2', 'r3'].map((room) => prefetchNewestBlocks(room).catch(() => {})))
    expect(listBlocks).toHaveBeenCalledTimes(3)
    expect(cachedWindow('r3')?.blocks.map((b) => b.id)).toEqual(['x'])
  })

  it('手上那份还新鲜就不再取', async () => {
    listBlocks.mockResolvedValue(page(['a']))
    await prefetchNewestBlocks('room')
    await prefetchNewestBlocks('room')
    expect(listBlocks).toHaveBeenCalledTimes(1)
  })
})
