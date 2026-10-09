/** 归档 / 取消归档走乐观更新：本地那一行不等服务端就先变，服务端那份回来覆盖，
 *  失败时还原（连带 rail 记着的话题）。 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  archiveTopic: vi.fn(),
  unarchiveTopic: vi.fn(),
  getTopic: vi.fn(),
  listTopics: vi.fn().mockResolvedValue({ data: [] }),
  listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
  listProjects: vi.fn().mockResolvedValue({ data: [] }),
  getTopicUnread: vi.fn().mockResolvedValue({}),
  getPrivateUnread: vi.fn().mockResolvedValue({}),
  getTopicNotifyLevels: vi.fn().mockResolvedValue({}),
}))

import type { Topic } from '@/cx_types'

import { archiveTopic, getTopic, listTopics, unarchiveTopic } from '@/api'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject } from '@/test/seedQueries'

const topic = (status: string): Topic =>
  ({ id: 't1', project_id: 'p', kind: 'topic', status, title: '房间', can_manage: true }) as Topic

// 服务器那一份：写成功了才变，侧栏重读读到的是它。
let server: Topic

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(listTopics).mockImplementation(() => Promise.resolve({ data: [server], total: 1 }))
})

async function flush() {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

async function opened(status = 'active') {
  server = topic(status)
  seedProject('p', { topics: [server] })
  const store = useWorkspaceStore()
  store.openProject('p')
  await flush()
  return store
}

/** 写请求先挂着；`succeed` 时服务器那一份才变成写后的样子。 */
function pendingWrite() {
  let succeed!: (t: Topic) => void
  let fail!: (e: unknown) => void
  const promise = new Promise<Topic>((resolve, reject) => {
    succeed = (t) => {
      server = t
      resolve(t)
    }
    fail = reject
  })
  return { promise, succeed, fail }
}

const statusOf = (store: ReturnType<typeof useWorkspaceStore>) => store.topics.find((t) => t.id === 't1')?.status

it('归档：本地这一行立刻变成已归档，成功用服务端那份覆盖', async () => {
  const store = await opened('active')
  const write = pendingWrite()
  vi.mocked(archiveTopic).mockReturnValue(write.promise)

  const done = store.archive('t1')
  await flush()
  // 请求还没回来，界面已经是归档后的样子
  expect(statusOf(store)).toBe('archived')

  write.succeed(topic('archived'))
  await done
  await flush()
  expect(archiveTopic).toHaveBeenCalledWith('t1')
  expect(statusOf(store)).toBe('archived')
})

it('归档失败：还原成原来那一行，并记下错误', async () => {
  const store = await opened('active')
  const write = pendingWrite()
  vi.mocked(archiveTopic).mockReturnValue(write.promise)

  const done = store.archive('t1')
  await flush()
  expect(statusOf(store)).toBe('archived')

  write.fail(new Error('boom'))
  await done
  await flush()
  expect(statusOf(store)).toBe('active')
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
  vi.mocked(archiveTopic).mockImplementation(() => {
    server = topic('archived')
    return Promise.resolve(server)
  })

  await store.archive('t1')
  await flush()
  expect(store.lastTopicIdFor('p')).toBeNull()
})

it('取消归档：本地先翻回在用，失败还原', async () => {
  const store = await opened('archived')
  const write = pendingWrite()
  vi.mocked(unarchiveTopic).mockReturnValue(write.promise)

  const done = store.unarchive('t1')
  await flush()
  expect(statusOf(store)).toBe('active')
  write.fail(new Error('no'))
  await done
  await flush()
  expect(statusOf(store)).toBe('archived')
  expect(unarchiveTopic).toHaveBeenCalledWith('t1')
})

it('归档之前发出的一行重读、归档之后才回来：不把这一行盖回归档之前', async () => {
  const store = await opened('active')
  let answer!: (t: Topic) => void
  vi.mocked(getTopic).mockReturnValue(new Promise((res) => (answer = res)))
  const reread = store.refreshTopicRow('t1')
  await flush()

  vi.mocked(archiveTopic).mockResolvedValue(topic('archived'))
  // 归档之后侧栏那次重读也先挂着：这里看的是晚回来的那一行自己会不会盖回去。
  let listed!: (payload: { data: Topic[]; total: number }) => void
  vi.mocked(listTopics).mockReturnValueOnce(new Promise((res) => (listed = res)))
  await store.archive('t1')
  answer(topic('active'))
  await reread
  await flush()

  expect(statusOf(store)).toBe('archived')
  listed({ data: [topic('archived')], total: 1 })
})
