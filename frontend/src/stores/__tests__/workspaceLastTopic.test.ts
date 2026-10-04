/** 从 rail 点回一个项目，落在上次打开的那个房间。
 *
 * 每天的主路径是「回到昨天那个房间」，先落到项目首页等于多加一跳。记着的那一个
 * 没了（删了、收成了别的房间、被归档）就回落项目首页，别把人送进一个打不开的房间。
 *
 * 这里钉的是行为：记住、拿出来、不在了就忘掉。
 */
import type { Topic } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

vi.mock('@/api', () => ({
  ApiError: class ApiError extends Error {},
  archiveTopic: vi.fn(),
  createTopic: vi.fn(),
  getPrivateUnread: vi.fn().mockResolvedValue({}),
  getProject: vi.fn(),
  getTopic: vi.fn(),
  getTopicNotifyLevels: vi.fn().mockResolvedValue({}),
  getTopicUnread: vi.fn().mockResolvedValue({}),
  isProjectArchivedError: () => false,
  listProjectMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listProjects: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listTopics: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  markTopicRead: vi.fn().mockResolvedValue(undefined),
  setTopicTitle: vi.fn(),
  unarchiveProject: vi.fn(),
  unarchiveTopic: vi.fn(),
  undoTopicTitle: vi.fn(),
  upgradeBlock: vi.fn(),
}))

import { archiveTopic, listTopics } from '@/api'
import { useWorkspaceStore } from '@/stores/workspace'

const topic = (id: string, projectId: string, status = 'active') =>
  ({ id, project_id: projectId, kind: 'topic', title: id, status }) as unknown as Topic

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

describe('记着每个项目上次打开的房间', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.mocked(listTopics).mockReset()
    vi.mocked(listTopics).mockResolvedValue({ data: [], total: 0 })
    vi.mocked(archiveTopic).mockReset()
  })

  it('记下的房间，下次进这个项目还认得出来', () => {
    const store = useWorkspaceStore()
    store.rememberTopic('p1', 't9')
    expect(store.lastTopicIdFor('p1')).toBe('t9')
    // 换一份 store（刷新页面）也读得回来：它写进了布局那份存储。
    setActivePinia(createPinia())
    expect(useWorkspaceStore().lastTopicIdFor('p1')).toBe('t9')
  })

  it('别的项目不受影响', () => {
    const store = useWorkspaceStore()
    store.rememberTopic('p1', 't9')
    expect(store.lastTopicIdFor('p2')).toBeNull()
  })

  it('记着的房间已经不在项目清单里了，就不再落回去', async () => {
    const store = useWorkspaceStore()
    store.rememberTopic('p1', 'gone')
    vi.mocked(listTopics).mockResolvedValue({ data: [topic('t1', 'p1')], total: 1 })

    await store.openProject('p1')
    await flush()

    expect(store.lastTopicIdFor('p1')).toBeNull()
  })

  it('归档掉的房间也不再落回去', async () => {
    const store = useWorkspaceStore()
    store.projectId = 'p1'
    store.topics = [topic('t9', 'p1')]
    store.rememberTopic('p1', 't9')
    vi.mocked(archiveTopic).mockResolvedValue(topic('t9', 'p1', 'archived'))

    await store.archive('t9')

    expect(store.lastTopicIdFor('p1')).toBeNull()
  })
})
