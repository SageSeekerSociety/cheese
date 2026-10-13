/** 打开项目那一刻，后台预取不能把后端的连接池打爆。
 *
 * 未读轮询顺手会去刷新「未读变多了」的那些话题的时间线缓存。刷新页面时未读表是
 * 空的，于是**每一个**有未读的话题都算「变多了」——一个两百多话题的项目会在同一
 * 瞬间打出几十个 GET /blocks，占满后端的数据库连接池，连用户此刻真正在等的那个
 * 请求一起挤掉（线上表现是一串 500，而且每次刷新必现）。
 *
 * 这里钉的是行为，不是实现：预取只发生在「这一趟真的开过」的话题上。
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

vi.mock('@/api', () => ({
  archiveTopic: vi.fn(),
  createTopic: vi.fn(),
  getPrivateUnread: vi.fn().mockResolvedValue({}),
  getTopic: vi.fn(),
  getTopicNotifyLevels: vi.fn().mockResolvedValue({}),
  getTopicUnread: vi.fn(),
  listProjectMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listProjects: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listTopics: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listBlocks: vi.fn().mockResolvedValue({ data: [], has_more: false }),
  markTopicRead: vi.fn().mockResolvedValue(undefined),
  setTopicTitle: vi.fn(),
  unarchiveTopic: vi.fn(),
  upgradeBlock: vi.fn(),
}))

import { getPrivateUnread, getTopicUnread, listBlocks } from '@/api'
import { setCachedWindow } from '@/query/blocks'
import { useWorkspaceStore } from '@/stores/workspace'
import { closeFirstPaint, openFirstPaint } from '@/test/firstPaint'

/** 打开项目（刷新页面后第一次进来），等第一次读完。 */
async function opened() {
  // 未读那几笔排在首帧之后（`lib/afterFirstPaint.ts`）：单测里没人画首帧，得先把那道门
  // 推开，否则它们一次都不发。
  await openFirstPaint()
  const store = useWorkspaceStore()
  store.openProject('p1')
  await flush()
  return store
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 一个刚刷新完页面、未读表还是空的项目，服务端说这些话题都有未读。 */
function unread(map: Record<string, number>) {
  vi.mocked(getTopicUnread).mockResolvedValue(
    Object.fromEntries(Object.entries(map).map(([id, n]) => [id, { count: n, new: true, messages: n }]))
  )
}

describe('未读轮询的后台预取', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(listBlocks).mockClear()
  })

  it('首帧之前不读未读：门开了才读', async () => {
    unread({ t1: 2 })
    // 这个文件里别的用例已经把门推开过，而门是全站一份状态、开着就一直开着（`@/test/firstPaint`）。
    closeFirstPaint()
    const store = useWorkspaceStore()
    store.openProject('p1')
    await flush()

    expect(vi.mocked(getTopicUnread)).not.toHaveBeenCalled()
    expect(store.unreadMap.t1).toBeUndefined()

    await openFirstPaint()
    await flush()

    expect(vi.mocked(getTopicUnread)).toHaveBeenCalled()
    expect(store.unreadMap.t1?.count).toBe(2)
  })

  it('私聊未读也等首帧：门开之前一笔不发，门开了只发一趟', async () => {
    // 同一道门上挂着三样（`stores/workspace.ts` 的 topic unread / private unread / notify
    // levels），上面那条只钉了第一样。这一样单独一条 query，拆掉它上面照样绿。
    closeFirstPaint()
    const store = useWorkspaceStore()
    store.openProject('p1')
    await flush()

    expect(vi.mocked(getPrivateUnread)).not.toHaveBeenCalled()

    await openFirstPaint()
    await flush()

    expect(vi.mocked(getPrivateUnread)).toHaveBeenCalledTimes(1)
  })

  it('刷新页面时不会为每个有未读的话题各发一个请求', async () => {
    unread(Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`t${i}`, 3])))
    const store = await opened()

    expect(vi.mocked(listBlocks)).not.toHaveBeenCalled()
    // 徽标本身照常更新——被砍掉的只有预取。
    expect(store.unreadMap.t7?.count).toBe(3)
  })

  it('这一趟开过的话题仍然会被后台刷新（切回去时第一帧就是新消息）', async () => {
    setCachedWindow('t1', { blocks: [], hasMore: false })
    unread({ t1: 2, t2: 5 })

    await opened()

    expect(vi.mocked(listBlocks).mock.calls.map((c) => c[0])).toEqual(['t1'])
  })

  it('未读没变多就不刷新', async () => {
    setCachedWindow('t1', { blocks: [], hasMore: false })
    unread({ t1: 2 })
    const store = await opened()
    vi.mocked(listBlocks).mockClear()

    await store.refreshUnread()
    await flush()

    expect(vi.mocked(listBlocks)).not.toHaveBeenCalled()
  })
})
