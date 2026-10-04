/** 工作面板那几份「每个话题一份」的数据的缓存。
 *
 * 钉的是：取过就记得（切回来先画上次那份）、同一份同时只发一条、失败不算数、最多记
 * 20 个话题、名册刚改过就不跟着改之前那条请求走、退出登录后在飞的请求不写回来。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getProgress = vi.fn()
const listTopicMembers = vi.fn()
const listRoomTasks = vi.fn()
vi.mock('@/api', () => ({
  getProgress: (...a: unknown[]) => getProgress(...a),
  listTopicMembers: (...a: unknown[]) => listTopicMembers(...a),
  listRoomTasks: (...a: unknown[]) => listRoomTasks(...a),
}))

import {
  cachedTopicPanel,
  clearTopicPanelCache,
  fetchRoomTasks,
  fetchTopicMembers,
  fetchTopicProgress,
} from './topicPanelCache'
import { announceTopicRosterChange } from './topicRosterChanges'

function deferred<T>() {
  let settle!: (v: T) => void
  const promise = new Promise<T>((res) => {
    settle = res
  })
  return { promise, settle }
}

const page = (n: number) => ({ data: Array.from({ length: n }, (_, i) => ({ id: `r${i}` })), has_more: false })

beforeEach(() => {
  vi.clearAllMocks()
  clearTopicPanelCache()
})

describe('话题面板缓存', () => {
  it('取过就记得，按话题和种类分开', async () => {
    expect(cachedTopicPanel('progress', 't1')).toBeUndefined()
    getProgress.mockResolvedValue({ items: [{ id: 'a' }] })
    await fetchTopicProgress('t1')
    expect(cachedTopicPanel('progress', 't1')).toEqual({ items: [{ id: 'a' }] })
    expect(cachedTopicPanel('members', 't1')).toBeUndefined()
    expect(cachedTopicPanel('progress', 't2')).toBeUndefined()
  })

  it('同一个话题的派出的活同时只发一条，且只要每条最新的一块', async () => {
    const d = deferred<ReturnType<typeof page>>()
    listRoomTasks.mockReturnValue(d.promise)
    const a = fetchRoomTasks('t1')
    const b = fetchRoomTasks('t1')
    expect(listRoomTasks).toHaveBeenCalledTimes(1)
    expect(listRoomTasks).toHaveBeenCalledWith('t1', { limit: 1 })
    d.settle(page(2))
    expect(await a).toBe(await b)
  })

  it('失败不写缓存，下一次重新发', async () => {
    listTopicMembers.mockRejectedValueOnce(new Error('boom'))
    await expect(fetchTopicMembers('t1')).rejects.toThrow('boom')
    expect(cachedTopicPanel('members', 't1')).toBeUndefined()
    listTopicMembers.mockResolvedValueOnce(page(1))
    await fetchTopicMembers('t1')
    expect(listTopicMembers).toHaveBeenCalledTimes(2)
    expect(cachedTopicPanel('members', 't1')?.data).toHaveLength(1)
  })

  it('最多记 20 个话题，丢的是最久没写过的那个', async () => {
    getProgress.mockResolvedValue({ items: [] })
    for (let i = 0; i < 21; i++) await fetchTopicProgress(`t${i}`)
    expect(cachedTopicPanel('progress', 't0')).toBeUndefined()
    expect(cachedTopicPanel('progress', 't1')).toBeDefined()
    expect(cachedTopicPanel('progress', 't20')).toBeDefined()
  })

  it('名册刚改过：改之前那条请求作废，下一位另发一条', async () => {
    const before = deferred<ReturnType<typeof page>>()
    listTopicMembers.mockReturnValueOnce(before.promise)
    const stale = fetchTopicMembers('t1')
    announceTopicRosterChange('t1')
    listTopicMembers.mockResolvedValueOnce(page(3))
    const fresh = await fetchTopicMembers('t1')
    expect(listTopicMembers).toHaveBeenCalledTimes(2)
    expect(fresh.data).toHaveLength(3)
    before.settle(page(1))
    await stale
    // 旧的那条后回来，也不能把旧名单写回去。
    expect(cachedTopicPanel('members', 't1')?.data).toHaveLength(3)
  })

  it('退出登录后，在飞的请求回来也不写回来', async () => {
    const d = deferred<{ items: never[] }>()
    getProgress.mockReturnValue(d.promise)
    const p = fetchTopicProgress('t1')
    clearTopicPanelCache()
    d.settle({ items: [] })
    await p
    expect(cachedTopicPanel('progress', 't1')).toBeUndefined()
  })
})
