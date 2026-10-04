/** 话题列表整块没读到：store 留下服务端那句原话，侧栏据此换成失败块（§3.10）。
 *
 *  401/403 不算「这一次没取到」——那是「进不来」，走 accessDenied 的一屏说明，不能
 *  被写成一颗按了没用的重试按钮。重试读成功就得把失败块收掉。
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

const listTopics = vi.fn()

vi.mock('@/api', () => ({
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      message = ''
    ) {
      super(message)
    }
  },
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
  listTopics: (...a: unknown[]) => listTopics(...a),
  markTopicRead: vi.fn().mockResolvedValue(undefined),
  setTopicTitle: vi.fn(),
  unarchiveProject: vi.fn(),
  unarchiveTopic: vi.fn(),
  undoTopicTitle: vi.fn(),
  upgradeBlock: vi.fn(),
}))

import { useWorkspaceStore } from '@/stores/workspace'

const root = { id: 'r', project_id: 'p1', kind: 'root', title: '总览', status: 'active' }

async function flush() {
  for (let i = 0; i < 12; i += 1) await new Promise((r) => setTimeout(r, 0))
}

describe('话题列表读失败留在侧栏那块地方', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    listTopics.mockReset()
  })

  it('失败时写下服务端原话，供侧栏换成失败块', async () => {
    listTopics.mockRejectedValue(new Error('HTTP 503 for /topics'))
    const store = useWorkspaceStore()
    await store.openProject('p1')
    await flush()
    expect(store.topicsError).toBe('HTTP 503 for /topics')
    expect(store.loadingTopics).toBe(false)
  })

  it('重试读到就把失败块收掉', async () => {
    listTopics.mockRejectedValueOnce(new Error('boom'))
    const store = useWorkspaceStore()
    await store.openProject('p1')
    await flush()
    expect(store.topicsError).toBe('boom')

    listTopics.mockResolvedValue({ data: [root], total: 1 })
    await store.reloadTopics()
    await flush()
    expect(store.topicsError).toBeNull()
    expect(store.topics).toHaveLength(1)
  })

  it('401/403 是「进不来」，走 accessDenied，不写成失败块', async () => {
    const { ApiError } = await import('@/api')
    listTopics.mockRejectedValue(new ApiError(403, 'forbidden'))
    const store = useWorkspaceStore()
    await store.openProject('p1')
    await flush()
    expect(store.accessDenied).toBe('forbidden')
    expect(store.topicsError).toBeNull()
  })
})
