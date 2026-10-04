/** 话题的归档 / 取消归档是就地发生的：这一行立刻挪进（或挪出）「已归档」，不等一次
 *  往返；服务器回来落实，失败放回原样并报错。 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listTopics = vi.fn()

vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  listTopics: (...a: unknown[]) => listTopics(...a),
  listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
  listProjects: vi.fn().mockResolvedValue({ data: [] }),
  getTopicUnread: vi.fn().mockResolvedValue({}),
  getPrivateUnread: vi.fn().mockResolvedValue({}),
  archiveTopic: vi.fn(),
  unarchiveTopic: vi.fn(),
}))

import type { Topic } from '@/cx_types'

import { ApiError, archiveTopic, unarchiveTopic } from '@/api'
import { useWorkspaceStore } from '@/stores/workspace'

const row = (status: string): Topic => ({ id: 't1', project_id: 'p', kind: 'topic', status, title: '房间' }) as Topic

/** 开了项目、清单里坐着这一个话题。 */
async function withTopic(status: string) {
  listTopics.mockResolvedValue({ data: [row(status)], total: 1 })
  const store = useWorkspaceStore()
  await store.openProject('p')
  expect(store.topics.find((r) => r.id === 't1')?.status).toBe(status)
  return store
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  listTopics.mockReset()
})

describe('归档一个话题', () => {
  it('请求还没回来，这一行已经是 archived', async () => {
    const store = await withTopic('active')
    // 归档后清单会给已归档的那一份；先摆好，好让成功后的后台刷新不再改回 active。
    listTopics.mockResolvedValue({ data: [row('archived')], total: 1 })
    let resolveApi: (t: Topic) => void = () => {}
    vi.mocked(archiveTopic).mockReturnValue(new Promise<Topic>((res) => (resolveApi = res)))

    const pending = store.archive('t1')
    // 乐观：还没等 PUT 回来。
    expect(store.topics.find((r) => r.id === 't1')?.status).toBe('archived')

    resolveApi(row('archived'))
    await pending
    expect(archiveTopic).toHaveBeenCalledWith('t1')
    expect(store.topics.find((r) => r.id === 't1')?.status).toBe('archived')
  })

  it('失败了：这一行放回 active，并记下错误', async () => {
    const store = await withTopic('active')
    vi.mocked(archiveTopic).mockRejectedValue(new ApiError(500, 'boom'))

    await store.archive('t1')

    expect(store.topics.find((r) => r.id === 't1')?.status).toBe('active')
    expect(store.error).toBeTruthy()
  })
})

describe('取消归档一个话题', () => {
  it('请求还没回来，这一行已经是 active', async () => {
    const store = await withTopic('archived')
    listTopics.mockResolvedValue({ data: [row('active')], total: 1 })
    let resolveApi: (t: Topic) => void = () => {}
    vi.mocked(unarchiveTopic).mockReturnValue(new Promise<Topic>((res) => (resolveApi = res)))

    const pending = store.unarchive('t1')
    expect(store.topics.find((r) => r.id === 't1')?.status).toBe('active')

    resolveApi(row('active'))
    await pending
    expect(unarchiveTopic).toHaveBeenCalledWith('t1')
  })

  it('失败了：这一行放回 archived', async () => {
    const store = await withTopic('archived')
    vi.mocked(unarchiveTopic).mockRejectedValue(new ApiError(500, 'boom'))

    await store.unarchive('t1')

    expect(store.topics.find((r) => r.id === 't1')?.status).toBe('archived')
    expect(store.error).toBeTruthy()
  })
})
