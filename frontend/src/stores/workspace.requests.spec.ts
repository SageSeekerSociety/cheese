import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  getTopic: vi.fn(),
  createTopic: vi.fn(),
  archiveTopic: vi.fn(),
  listTopics: vi.fn().mockResolvedValue({ data: [] }),
  listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
  listProjects: vi.fn().mockResolvedValue({ data: [] }),
  getTopicNotifyLevels: vi.fn().mockResolvedValue({}),
  getTopicUnread: vi.fn().mockResolvedValue({}),
  getPrivateUnread: vi.fn().mockResolvedValue({}),
}))

import type { TopicUnread } from '@/api'
import type { Topic } from '@/cx_types'

import { archiveTopic, createTopic, getTopic, getTopicUnread, listTopics } from '@/api'
import { useWorkspaceStore } from '@/stores/workspace'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
const room = (id: string, project_id: string) => ({ id, project_id, title: id }) as Topic
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

it('a delayed room lookup stays in its original project', async () => {
  const store = useWorkspaceStore()
  await store.openProject('a')
  const response = deferred<Topic>()
  vi.mocked(getTopic).mockReturnValueOnce(response.promise)
  const lookup = store.loadPlace('a-room')
  await store.openProject('b')
  response.resolve(room('a-room', 'a'))
  await lookup
  expect(store.topics).toEqual([])
})

it('creating a room during navigation leaves the destination project intact', async () => {
  const store = useWorkspaceStore()
  await store.openProject('a')
  const response = deferred<Topic>()
  vi.mocked(createTopic).mockReturnValueOnce(response.promise)
  const creating = store.create('a-room')
  await store.openProject('b')
  response.resolve(room('a-room', 'a'))
  expect(await creating).toBeNull()
  expect(store.topics).toEqual([])
})

// 频道要先起名：没打字就不建，也不替它写一个标题。
it('a channel without a name is not created', async () => {
  const store = useWorkspaceStore()
  await store.openProject('a')
  expect(await store.create('   ')).toBeNull()
  expect(createTopic).not.toHaveBeenCalled()
})

it('overlapping topic refreshes share one pending request', async () => {
  const store = useWorkspaceStore()
  await store.openProject('a')
  vi.mocked(listTopics).mockClear()
  const response = deferred<Awaited<ReturnType<typeof listTopics>>>()
  vi.mocked(listTopics).mockReturnValueOnce(response.promise)
  const requests = Array.from({ length: 8 }, () => store.refreshTopics())
  expect(listTopics).toHaveBeenCalledTimes(1)
  response.resolve({ data: [], total: 0 })
  await Promise.all(requests)
})

it('a refresh asked for while an older read is in flight reads the list again', async () => {
  const store = useWorkspaceStore()
  await store.openProject('a')
  const before = deferred<Awaited<ReturnType<typeof listTopics>>>()
  const after = deferred<Awaited<ReturnType<typeof listTopics>>>()
  vi.mocked(listTopics).mockClear().mockReturnValueOnce(before.promise).mockReturnValueOnce(after.promise)
  // A turn ends and the sidebar starts reading; the platform then names the
  // room and says so while that read is still on its way back.
  try {
    const onTurnDone = store.refreshTopics()
    const onRenamed = store.refreshTopics()
    before.resolve({ data: [{ ...room('r', 'a'), title: '新话题' }], total: 1 })
    await onTurnDone
    after.resolve({ data: [{ ...room('r', 'a'), title: 'Named' }], total: 1 })
    await onRenamed
    expect(store.topics.map((topic) => topic.title)).toEqual(['Named'])
  } finally {
    // An answer this test queued but never asked for must not reach the next one.
    vi.mocked(listTopics)
      .mockReset()
      .mockResolvedValue({ data: [] } as never)
  }
})

it('overlapping unread refreshes share one pending request', async () => {
  const store = useWorkspaceStore()
  store.projectId = 'a'
  const response = deferred<Record<string, TopicUnread>>()
  vi.mocked(getTopicUnread).mockReturnValueOnce(response.promise)
  const requests = Array.from({ length: 8 }, () => store.refreshUnread())
  expect(getTopicUnread).toHaveBeenCalledTimes(1)
  response.resolve({})
  await Promise.all(requests)
})

it('an old project failure leaves the current project error clear', async () => {
  const store = useWorkspaceStore()
  const response = deferred<Awaited<ReturnType<typeof listTopics>>>()
  vi.mocked(listTopics).mockReturnValueOnce(response.promise)
  const old = store.openProject('a')
  await store.openProject('b')
  response.reject(new Error('old project timed out'))
  await old
  expect(store.error).toBeNull()
})

it('a list requested before creation cannot remove the newly created room', async () => {
  const store = useWorkspaceStore()
  await store.openProject('a')
  const response = deferred<Awaited<ReturnType<typeof listTopics>>>()
  vi.mocked(listTopics).mockReturnValueOnce(response.promise)
  const refreshing = store.refreshTopics()
  vi.mocked(createTopic).mockResolvedValueOnce(room('new', 'a'))
  await store.create('new')
  response.resolve({ data: [], total: 0 })
  await refreshing
  expect(store.topics.map((topic) => topic.id)).toEqual(['new'])
})

it('archiving finishes when the write succeeds while the sidebar refresh is still pending', async () => {
  const store = useWorkspaceStore()
  await store.openProject('a')
  store.topics = [room('old', 'a')]
  const response = deferred<Awaited<ReturnType<typeof listTopics>>>()
  vi.mocked(listTopics).mockReturnValueOnce(response.promise)
  vi.mocked(archiveTopic).mockResolvedValueOnce({ ...room('old', 'a'), status: 'archived' })
  await store.archive('old')
  expect(store.topics[0].status).toBe('archived')
  response.resolve({ data: [], total: 0 })
})

it('a refresh after archiving does not reuse a pre-archive list response', async () => {
  const store = useWorkspaceStore()
  await store.openProject('a')
  store.topics = [room('old', 'a')]
  const old = deferred<Awaited<ReturnType<typeof listTopics>>>()
  const fresh = deferred<Awaited<ReturnType<typeof listTopics>>>()
  vi.mocked(listTopics).mockClear().mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const refreshing = store.refreshTopics()
  vi.mocked(archiveTopic).mockResolvedValueOnce({ ...room('old', 'a'), status: 'archived' })
  await store.archive('old')
  expect(listTopics).toHaveBeenCalledTimes(2)
  old.resolve({ data: [room('old', 'a')], total: 1 })
  await refreshing
  expect(store.topics[0].status).toBe('archived')
  fresh.resolve({ data: [{ ...room('old', 'a'), status: 'archived' }], total: 1 })
})

it('shows the new room first and refreshes the sidebar without delaying navigation', async () => {
  const store = useWorkspaceStore()
  await store.openProject('a')
  store.topics = [room('older', 'a')]
  const fresh = deferred<Awaited<ReturnType<typeof listTopics>>>()
  vi.mocked(listTopics).mockReturnValueOnce(fresh.promise)
  const created = { ...room('new', 'a'), i_participate: true }
  vi.mocked(createTopic).mockResolvedValueOnce(created)
  expect(await store.create('new')).toEqual(created)
  expect(store.topics.map((topic) => topic.id)).toEqual(['new', 'older'])
  fresh.resolve({ data: [created, room('older', 'a')], total: 2 })
})

it('a row named by id is read back on its own, and no other row moves', async () => {
  const store = useWorkspaceStore()
  await store.openProject('a')
  store.topics = [room('r1', 'a'), room('r2', 'a')]
  vi.mocked(listTopics).mockClear()
  vi.mocked(getTopic).mockResolvedValueOnce({ ...room('r2', 'a'), title: '改过了' })

  await store.refreshTopicRow('r2')

  expect(store.topics.map((topic) => topic.title)).toEqual(['r1', '改过了'])
  // The whole list was NOT read again — that is the point of naming the row.
  expect(listTopics).not.toHaveBeenCalled()
})

it('a named row this list does not hold yet falls back to reading the list', async () => {
  const store = useWorkspaceStore()
  await store.openProject('a')
  store.topics = [room('r1', 'a')]
  vi.mocked(getTopic).mockResolvedValueOnce(room('r2', 'a'))
  vi.mocked(listTopics)
    .mockClear()
    .mockResolvedValueOnce({ data: [room('r1', 'a'), room('r2', 'a')], total: 2 } as never)

  await store.refreshTopicRow('r2')

  // Joining a private channel can name a row this sidebar never held; only a
  // whole-list read can bring it in.
  expect(store.topics.map((topic) => topic.id)).toEqual(['r1', 'r2'])
})
