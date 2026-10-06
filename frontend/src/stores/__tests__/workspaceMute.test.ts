/** 频道通知档位和「全部标为已读」在本地的那一半。
 *
 * 行上的数字由后端按档位算好；本地记着我在哪个频道不是默认档位，改档位先改本地、
 * 失败了改回去。全部标为已读先清掉本地角标，失败了恢复。
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

import type { TopicUnread } from '@/api'

import { getTopicNotifyLevels, getTopicUnread, markAllTopicsRead, setTopicNotifyLevel } from '@/api'
import { useWorkspaceStore } from '@/stores/workspace'

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

const n = (count: number): TopicUnread => ({ count, new: true, messages: count })

describe('频道通知档位', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getTopicUnread).mockResolvedValue({ t1: n(3), t2: n(5) })
    vi.mocked(getTopicNotifyLevels).mockResolvedValue({ t2: { level: 'mute', muted_until: null } })
  })

  it('没列出来的频道是默认档位，列出来的照列出来的算', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    await store.refreshUnread()
    await flush()
    expect(store.levelOf('t1')).toBe('mentions')
    expect(store.isMuted('t2')).toBe(true)
  })

  it('改档位立刻生效；保存失败就改回原来的档位', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    await store.refreshUnread()
    await flush()
    vi.mocked(setTopicNotifyLevel).mockResolvedValueOnce({})
    await store.setNotifyLevel('t2', 'all')
    expect(setTopicNotifyLevel).toHaveBeenCalledWith('t2', 'all', null)
    expect(store.levelOf('t2')).toBe('all')

    vi.mocked(setTopicNotifyLevel).mockRejectedValueOnce(new Error('boom'))
    await store.setNotifyLevel('t1', 'mute', '2030-01-01T00:00:00.000Z')
    expect(store.levelOf('t1')).toBe('mentions')
  })

  it('静音的截止时间跟着设置走，别的档位不带', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    vi.mocked(setTopicNotifyLevel).mockResolvedValue({})
    await store.setNotifyLevel('t1', 'mute', '2030-01-01T00:00:00.000Z')
    expect(store.mutedUntil('t1')).toBe('2030-01-01T00:00:00.000Z')
    await store.setNotifyLevel('t1', 'all', '2030-01-01T00:00:00.000Z')
    expect(setTopicNotifyLevel).toHaveBeenLastCalledWith('t1', 'all', null)
    expect(store.mutedUntil('t1')).toBeNull()
  })
})

describe('全部标为已读', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getTopicUnread).mockResolvedValue({ t1: n(3), t2: n(5) })
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
    vi.mocked(getTopicUnread).mockResolvedValue({ t1: n(4) })
    await store.markAllRead()
    await flush()
    expect(store.unreadMap).toEqual({ t1: n(4) })
  })

  it('点之前已经在飞的那次轮询回来，不把清掉的角标盖回去', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    let answer!: (v: Record<string, TopicUnread>) => void
    vi.mocked(getTopicUnread).mockReturnValueOnce(new Promise((r) => (answer = r)))
    const polling = store.refreshUnread()
    vi.mocked(markAllTopicsRead).mockResolvedValueOnce({ topic_ids: ['t1', 't2'] })
    vi.mocked(getTopicUnread).mockResolvedValue({})
    await store.markAllRead()
    answer({ t1: n(3), t2: n(5) })
    await polling
    await flush()
    expect(store.unreadMap).toEqual({})
  })
})
