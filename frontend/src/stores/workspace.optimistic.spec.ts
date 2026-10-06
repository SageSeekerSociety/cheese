/** 归档 / 取消归档走乐观更新：本地那一行不等服务端就先变，服务端那份回来覆盖，
 *  失败时还原（连带 rail 记着的话题）。 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  archiveTopic: vi.fn(),
  unarchiveTopic: vi.fn(),
  listTopics: vi.fn().mockResolvedValue({ data: [] }),
  listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
  listProjects: vi.fn().mockResolvedValue({ data: [] }),
  getTopicUnread: vi.fn().mockResolvedValue({}),
  getPrivateUnread: vi.fn().mockResolvedValue({}),
  getTopicNotifyLevels: vi.fn().mockResolvedValue({}),
}))

import type { Topic } from '@/cx_types'

import { archiveTopic, listTopics, unarchiveTopic } from '@/api'
import { useWorkspaceStore } from '@/stores/workspace'

const topic = (status: string): Topic =>
  ({ id: 't1', project_id: 'p', kind: 'topic', status, title: '房间', can_manage: true }) as Topic

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0))
}

async function opened(status = 'active') {
  vi.mocked(listTopics).mockResolvedValue({ data: [topic(status)], total: 1 })
  const store = useWorkspaceStore()
  await store.openProject('p')
  await settle()
  return store
}

it('归档：本地这一行立刻变成已归档，成功用服务端那份覆盖', async () => {
  const store = await opened('active')
  let release!: (t: Topic) => void
  vi.mocked(archiveTopic).mockReturnValue(new Promise((res) => (release = res)))

  const done = store.archive('t1')
  // 请求还没回来，界面已经是归档后的样子
  expect(store.topics.find((t) => t.id === 't1')?.status).toBe('archived')

  release(topic('archived'))
  await done
  expect(archiveTopic).toHaveBeenCalledWith('t1')
  expect(store.topics.find((t) => t.id === 't1')?.status).toBe('archived')
})

it('归档失败：还原成原来那一行，并记下错误', async () => {
  const store = await opened('active')
  vi.mocked(archiveTopic).mockRejectedValue(new Error('boom'))

  const done = store.archive('t1')
  expect(store.topics.find((t) => t.id === 't1')?.status).toBe('archived')

  await done
  expect(store.topics.find((t) => t.id === 't1')?.status).toBe('active')
  expect(store.error).toBe('boom')
})

it('归档正记着的那一个：rail 当场忘掉它；失败时退回去', async () => {
  const store = await opened('active')
  store.rememberTopic('p', 't1')
  expect(store.lastTopicIdFor('p')).toBe('t1')

  vi.mocked(archiveTopic).mockRejectedValue(new Error('boom'))
  await store.archive('t1')
  // 失败：rail 还该落回它
  expect(store.lastTopicIdFor('p')).toBe('t1')
})

it('归档成功：rail 不再落回已归档的那一个', async () => {
  const store = await opened('active')
  store.rememberTopic('p', 't1')
  vi.mocked(archiveTopic).mockResolvedValue(topic('archived'))

  await store.archive('t1')
  expect(store.lastTopicIdFor('p')).toBeNull()
})

it('取消归档：本地先翻回在用，失败还原', async () => {
  const store = await opened('archived')
  vi.mocked(unarchiveTopic).mockRejectedValue(new Error('no'))

  const done = store.unarchive('t1')
  expect(store.topics.find((t) => t.id === 't1')?.status).toBe('active')
  await done
  expect(store.topics.find((t) => t.id === 't1')?.status).toBe('archived')
  expect(unarchiveTopic).toHaveBeenCalledWith('t1')
})
