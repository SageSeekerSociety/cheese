/** 话题静音和「全部标为已读」在本地的那一半。
 *
 * 静音的房间未读照样记着（打开时「新消息从哪开始」那条线要用），但不进侧栏用的那张
 * 角标表；全部标为已读先清掉本地角标，失败了恢复。
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

vi.mock('@/api', () => ({
  ApiError: class extends Error {},
  isProjectArchivedError: () => false,
  getPrivateUnread: vi.fn().mockResolvedValue({}),
  getTopicUnread: vi.fn(),
  getTopicNotifyLevels: vi.fn(),
  setTopicNotifyLevel: vi.fn(),
  markAllTopicsRead: vi.fn(),
  markTopicRead: vi.fn().mockResolvedValue(undefined),
  listBlocks: vi.fn().mockResolvedValue({ data: [], has_more: false }),
}))

import { getTopicNotifyLevels, getTopicUnread, markAllTopicsRead, setTopicNotifyLevel } from '@/api'
import { useWorkspaceStore } from '@/stores/workspace'

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

describe('话题静音', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getTopicUnread).mockResolvedValue({ t1: 3, t2: 5 })
    vi.mocked(getTopicNotifyLevels).mockResolvedValue({ t2: 'mute' })
  })

  it('静音的房间不进角标表，但未读还记着', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    await store.refreshUnread()
    await flush()
    expect(store.unreadMap).toEqual({ t1: 3, t2: 5 })
    expect(store.badgeUnreadMap).toEqual({ t1: 3 })
    expect(store.isMuted('t2')).toBe(true)
  })

  it('取消静音立刻回到角标表；保存失败就改回去', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    await store.refreshUnread()
    await flush()
    vi.mocked(setTopicNotifyLevel).mockResolvedValueOnce({})
    await store.setMuted('t2', false)
    expect(setTopicNotifyLevel).toHaveBeenCalledWith('t2', 'all')
    expect(store.badgeUnreadMap).toEqual({ t1: 3, t2: 5 })

    vi.mocked(setTopicNotifyLevel).mockRejectedValueOnce(new Error('boom'))
    await store.setMuted('t1', true)
    expect(store.isMuted('t1')).toBe(false)
  })
})

describe('全部标为已读', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getTopicUnread).mockResolvedValue({ t1: 3, t2: 5 })
    vi.mocked(getTopicNotifyLevels).mockResolvedValue({})
  })

  it('先清本地角标，再让服务器推已读位', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    await store.refreshUnread()
    await flush()
    vi.mocked(markAllTopicsRead).mockResolvedValueOnce({ topic_ids: ['t1', 't2'] })
    await store.markAllRead()
    expect(markAllTopicsRead).toHaveBeenCalledWith('p1')
    expect(store.unreadMap).toEqual({})
  })

  it('失败了按服务器此刻的数把角标拉回来', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    await store.refreshUnread()
    await flush()
    vi.mocked(markAllTopicsRead).mockRejectedValueOnce(new Error('boom'))
    vi.mocked(getTopicUnread).mockResolvedValue({ t1: 4 })
    await store.markAllRead()
    await flush()
    expect(store.unreadMap).toEqual({ t1: 4 })
  })

  it('点之前已经在飞的那次轮询回来，不把清掉的角标盖回去', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    let answer!: (v: Record<string, number>) => void
    vi.mocked(getTopicUnread).mockReturnValueOnce(new Promise((r) => (answer = r)))
    const polling = store.refreshUnread()
    vi.mocked(markAllTopicsRead).mockResolvedValueOnce({ topic_ids: ['t1', 't2'] })
    vi.mocked(getTopicUnread).mockResolvedValue({})
    await store.markAllRead()
    answer({ t1: 3, t2: 5 })
    await polling
    await flush()
    expect(store.unreadMap).toEqual({})
  })
})
