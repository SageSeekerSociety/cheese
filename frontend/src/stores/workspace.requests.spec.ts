import { effectScope, ref } from 'vue'
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

import type { Topic } from '@/cx_types'

import { archiveTopic, createTopic, getTopic, listTopics } from '@/api'
import { usePlace } from '@/query/project'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject } from '@/test/seedQueries'

type TopicList = Awaited<ReturnType<typeof listTopics>>

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
const list = (...rows: Topic[]): TopicList => ({ data: rows, total: rows.length })

async function flush() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 打开项目，等它第一次读完。 */
async function opened(project: string, rows: Topic[] = []) {
  if (rows.length) seedProject(project, { topics: rows })
  const store = useWorkspaceStore()
  store.openProject(project)
  await flush()
  return store
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(listTopics).mockReset().mockResolvedValue(list())
})

// 深链接进来、清单里还没有的房间单独取一行。取的时候人已经去了别的项目：取回来的
// 那一行属于原来那个项目，不能当成现在这个项目的房间。
it('a delayed room lookup stays in its original project', async () => {
  const response = deferred<Topic>()
  vi.mocked(getTopic).mockReturnValueOnce(response.promise)
  const projectId = ref<string | null>('a')
  const scope = effectScope()
  const { place, resolving } = scope.run(() => usePlace(projectId, ref('a-room'), ref<Topic[]>([])))!
  await flush()
  expect(resolving.value).toBe(true)
  projectId.value = 'b'
  response.resolve(room('a-room', 'a'))
  await flush()
  expect(place.value).toBeNull()
  scope.stop()
})

it('creating a room during navigation leaves the destination project intact', async () => {
  const store = await opened('a')
  const response = deferred<Topic>()
  vi.mocked(createTopic).mockReturnValueOnce(response.promise)
  const creating = store.create('a-room')
  store.openProject('b')
  await flush()
  response.resolve(room('a-room', 'a'))
  expect(await creating).toBeNull()
  await flush()
  expect(store.topics).toEqual([])
})

// 频道要先起名：没打字就不建，也不替它写一个标题。
it('a channel without a name is not created', async () => {
  const store = await opened('a')
  expect(await store.create('   ')).toBeNull()
  expect(createTopic).not.toHaveBeenCalled()
})

it('a refresh asked for while an older read is in flight reads the list again', async () => {
  const store = await opened('a', [room('r', 'a')])
  const before = deferred<TopicList>()
  const after = deferred<TopicList>()
  vi.mocked(listTopics).mockReturnValueOnce(before.promise).mockReturnValueOnce(after.promise)
  // A turn ends and the sidebar starts reading; the platform then names the
  // room and says so while that read is still on its way back.
  void store.refreshTopics()
  await flush()
  void store.refreshTopics()
  await flush()
  before.resolve(list({ ...room('r', 'a'), title: '新话题' }))
  await flush()
  after.resolve(list({ ...room('r', 'a'), title: 'Named' }))
  await flush()
  expect(store.topics.map((topic) => topic.title)).toEqual(['Named'])
})

it('an old project failure leaves the current project error clear', async () => {
  const response = deferred<TopicList>()
  vi.mocked(listTopics).mockReturnValueOnce(response.promise)
  const store = await opened('a')
  store.openProject('b')
  await flush()
  response.reject(new Error('old project timed out'))
  await flush()
  expect(store.error).toBeNull()
  expect(store.topicsError).toBeNull()
})

it('a list requested before creation cannot remove the newly created room', async () => {
  const store = await opened('a', [room('older', 'a')])
  const stale = deferred<TopicList>()
  const fresh = deferred<TopicList>()
  vi.mocked(listTopics).mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise)
  const refreshing = store.refreshTopics()
  vi.mocked(createTopic).mockResolvedValueOnce(room('new', 'a'))
  await store.create('new')
  stale.resolve(list(room('older', 'a')))
  await refreshing
  await flush()
  expect(store.topics.map((topic) => topic.id)).toEqual(['new', 'older'])
  fresh.resolve(list(room('new', 'a'), room('older', 'a')))
})

it('archiving finishes when the write succeeds while the sidebar refresh is still pending', async () => {
  const store = await opened('a', [room('old', 'a')])
  const response = deferred<TopicList>()
  vi.mocked(listTopics).mockReturnValueOnce(response.promise)
  vi.mocked(archiveTopic).mockResolvedValueOnce({ ...room('old', 'a'), status: 'archived' })
  await store.archive('old')
  expect(store.topics[0].status).toBe('archived')
  response.resolve(list({ ...room('old', 'a'), status: 'archived' }))
})

it('a refresh after archiving does not reuse a pre-archive list response', async () => {
  const store = await opened('a', [room('old', 'a')])
  const old = deferred<TopicList>()
  const fresh = deferred<TopicList>()
  vi.mocked(listTopics).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const refreshing = store.refreshTopics()
  vi.mocked(archiveTopic).mockResolvedValueOnce({ ...room('old', 'a'), status: 'archived' })
  await store.archive('old')
  old.resolve(list(room('old', 'a')))
  await refreshing
  await flush()
  expect(store.topics[0].status).toBe('archived')
  fresh.resolve(list({ ...room('old', 'a'), status: 'archived' }))
  await flush()
  expect(store.topics[0].status).toBe('archived')
})

it('shows the new room first and refreshes the sidebar without delaying navigation', async () => {
  const store = await opened('a', [room('older', 'a')])
  const fresh = deferred<TopicList>()
  vi.mocked(listTopics).mockReturnValueOnce(fresh.promise)
  const created = { ...room('new', 'a'), i_participate: true }
  vi.mocked(createTopic).mockResolvedValueOnce(created)
  expect(await store.create('new')).toEqual(created)
  expect(store.topics.map((topic) => topic.id)).toEqual(['new', 'older'])
  fresh.resolve(list(created, room('older', 'a')))
})

it('a row named by id is read back on its own, and no other row moves', async () => {
  const store = await opened('a', [room('r1', 'a'), room('r2', 'a')])
  vi.mocked(listTopics).mockClear()
  vi.mocked(getTopic).mockResolvedValueOnce({ ...room('r2', 'a'), title: '改过了' })

  await store.refreshTopicRow('r2')

  expect(store.topics.map((topic) => topic.title)).toEqual(['r1', '改过了'])
  // The whole list was NOT read again — that is the point of naming the row.
  expect(listTopics).not.toHaveBeenCalled()
})

it('a named row this list does not hold yet falls back to reading the list', async () => {
  const store = await opened('a', [room('r1', 'a')])
  vi.mocked(getTopic).mockResolvedValueOnce(room('r2', 'a'))
  vi.mocked(listTopics).mockResolvedValueOnce(list(room('r1', 'a'), room('r2', 'a')))

  await store.refreshTopicRow('r2')
  await flush()

  // Joining a private channel can name a row this sidebar never held; only a
  // whole-list read can bring it in.
  expect(store.topics.map((topic) => topic.id)).toEqual(['r1', 'r2'])
})

// 一串连着来的「变了」（推送、轮询、几处一起要）：在路上的那一次是变之前发出去的，
// 等它回来再读一次；这期间再要的都跟着那一次，不是来一个读一次整份清单。
it('a burst of refreshes while a read is in flight costs one more read, which has the last word', async () => {
  const store = await opened('a')
  const first = deferred<TopicList>()
  const second = deferred<TopicList>()
  vi.mocked(listTopics).mockReset().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
  void store.refreshTopics()
  await flush()
  const burst = Array.from({ length: 8 }, () => store.refreshTopics())
  first.resolve(list(room('old', 'a')))
  await flush()
  second.resolve(list(room('new', 'a')))
  await Promise.all(burst)
  await flush()
  expect(listTopics).toHaveBeenCalledTimes(2)
  expect(store.topics.map((t) => t.id)).toEqual(['new'])
})
