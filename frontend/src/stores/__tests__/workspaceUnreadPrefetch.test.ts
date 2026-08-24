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
  getTopicUnread: vi.fn(),
  listProjectMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listProjects: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listTopics: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listBlocks: vi.fn().mockResolvedValue({ data: [], has_more: false }),
  markTopicRead: vi.fn().mockResolvedValue(undefined),
  setTopicTitle: vi.fn(),
  splitTopic: vi.fn(),
  unarchiveTopic: vi.fn(),
  upgradeBlock: vi.fn(),
}))

import { getTopicUnread, listBlocks } from '@/api'
import { blockCache, blockHasMore, setCachedWindow } from '@/lib/blockCache'
import { useWorkspaceStore } from '@/stores/workspace'

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 一个刚刷新完页面、未读表还是空的项目，服务端说这些话题都有未读。 */
function unread(map: Record<string, number>) {
  vi.mocked(getTopicUnread).mockResolvedValue(map)
}

describe('未读轮询的后台预取', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    blockCache.clear()
    blockHasMore.clear()
    vi.mocked(listBlocks).mockClear()
  })

  it('刷新页面时不会为每个有未读的话题各发一个请求', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    unread(Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`t${i}`, 3])))

    await store.refreshUnread()
    await flush()

    expect(vi.mocked(listBlocks)).not.toHaveBeenCalled()
    // 徽标本身照常更新——被砍掉的只有预取。
    expect(store.unreadMap.t7).toBe(3)
  })

  it('这一趟开过的话题仍然会被后台刷新（切回去时第一帧就是新消息）', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    setCachedWindow('t1', { blocks: [], hasMore: false })
    unread({ t1: 2, t2: 5 })

    await store.refreshUnread()
    await flush()

    expect(vi.mocked(listBlocks).mock.calls.map((c) => c[0])).toEqual(['t1'])
  })

  it('未读没变多就不刷新', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    setCachedWindow('t1', { blocks: [], hasMore: false })
    unread({ t1: 2 })
    await store.refreshUnread()
    await flush()
    vi.mocked(listBlocks).mockClear()

    await store.refreshUnread()
    await flush()

    expect(vi.mocked(listBlocks)).not.toHaveBeenCalled()
  })
})
