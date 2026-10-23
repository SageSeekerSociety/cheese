/** 从 rail 点回一个项目，落在上次待的那个地方。
 *
 * 每天的主路径是「回到昨天那个房间」——而昨天可能一直在某件任务里，所以记的不能
 * 只是频道：只记频道，回来就只到频道、到不了那件任务。记着的那一个没了（删了、
 * 收成了别的房间、被归档）就回落项目首页，别把人送进一个打不开的地方。
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
import { seedProject } from '@/test/seedQueries'

const topic = (id: string, projectId: string, status = 'active') =>
  ({ id, project_id: projectId, kind: 'topic', title: id, status }) as unknown as Topic

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

describe('记着每个项目上次待的地方', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.mocked(listTopics).mockReset()
    vi.mocked(listTopics).mockResolvedValue({ data: [], total: 0 })
    vi.mocked(archiveTopic).mockReset()
  })

  it('记下的地方，下次进这个项目还认得出来', () => {
    const store = useWorkspaceStore()
    store.rememberPlace('p1', { path: 'channels/t9', room: 't9' })
    expect(store.placeFor('p1')).toEqual({ path: 'channels/t9', room: 't9' })
    // 换一份 store（刷新页面）也读得回来：它写进了布局那份存储。
    setActivePinia(createPinia())
    expect(useWorkspaceStore().placeFor('p1')).toEqual({ path: 'channels/t9', room: 't9' })
  })

  it('任务页记的是那件任务，连它所在的频道一起', () => {
    const store = useWorkspaceStore()
    store.rememberPlace('p1', { path: 'tasks/task-1', room: 't9' })
    // 回到项目要落回的是任务，不是它所在的频道——但频道也得记着：频道没了，任务
    // 页那个地址就画不出来，所以校验的是它。
    expect(store.placeFor('p1')).toEqual({ path: 'tasks/task-1', room: 't9' })
  })

  // 项目里别的页也是一样记：成员页不在任何频道里，所以没有频道可校验。
  it('项目里的其它页（成员、总览、资料库……）同样记得住', () => {
    const store = useWorkspaceStore()
    store.rememberPlace('p1', { path: 'members/lisi', room: null })
    expect(store.placeFor('p1')).toEqual({ path: 'members/lisi', room: null })
  })

  it('别的项目不受影响', () => {
    const store = useWorkspaceStore()
    store.rememberPlace('p1', { path: 'channels/t9', room: 't9' })
    expect(store.placeFor('p2')).toBeNull()
  })

  it('记着的那个频道已经不在项目清单里了，就不再落回去', async () => {
    const store = useWorkspaceStore()
    store.rememberPlace('p1', { path: 'channels/gone', room: 'gone' })
    vi.mocked(listTopics).mockResolvedValue({ data: [topic('t1', 'p1')], total: 1 })

    store.openProject('p1')
    await flush()

    expect(store.placeFor('p1')).toBeNull()
  })

  // 记着的是任务时，任务本身没了不一定读得出来（清单里没有任务），但任务所在的
  // 频道没了就一定读得出来：那时候记的地方已经打不开了，忘掉它。
  it('记着的任务，它所在的频道没了，也不再落回去', async () => {
    const store = useWorkspaceStore()
    store.rememberPlace('p1', { path: 'tasks/task-1', room: 'gone' })
    vi.mocked(listTopics).mockResolvedValue({ data: [topic('t1', 'p1')], total: 1 })

    store.openProject('p1')
    await flush()

    expect(store.placeFor('p1')).toBeNull()
  })

  // 不在频道里的页（总览、成员、资料库）不靠频道活着：清单读回来照旧留着它。
  it('不在频道里的那一页，清单读回来照旧留着', async () => {
    const store = useWorkspaceStore()
    store.rememberPlace('p1', { path: 'members/lisi', room: null })
    vi.mocked(listTopics).mockResolvedValue({ data: [topic('t1', 'p1')], total: 1 })

    store.openProject('p1')
    await flush()

    expect(store.placeFor('p1')).toEqual({ path: 'members/lisi', room: null })
  })

  it('归档掉的频道也不再落回去', async () => {
    seedProject('p1', { topics: [topic('t9', 'p1')] })
    const store = useWorkspaceStore()
    store.openProject('p1')
    await flush()
    store.rememberPlace('p1', { path: 'channels/t9', room: 't9' })
    // 归档的话题还在清单里（只是状态变了）。
    vi.mocked(listTopics).mockResolvedValue({ data: [topic('t9', 'p1', 'archived')], total: 1 })
    vi.mocked(archiveTopic).mockResolvedValue(topic('t9', 'p1', 'archived'))

    await store.archive('t9')
    await flush()

    expect(store.placeFor('p1')).toBeNull()
  })
})
