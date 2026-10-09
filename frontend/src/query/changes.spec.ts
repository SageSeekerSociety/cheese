// 房间推来「某样东西变了」之后，缓存里那几份怎么跟上。
import type { Topic } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

const getTopic = vi.fn()
const listTopics = vi.fn()
const setTopicTitle = vi.fn()
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  getTopic: (...args: unknown[]) => getTopic(...args),
  listTopics: (...args: unknown[]) => listTopics(...args),
  setTopicTitle: (...args: unknown[]) => setTopicTitle(...args),
}))

const { queryClient } = await import('@/query/client')
const { keys } = await import('@/query/keys')
const { roomChanged } = await import('@/query/changes')
const { seedProject, seedProjects } = await import('@/test/seedQueries')
const { useWorkspaceStore } = await import('@/stores/workspace')

function topic(id: string, title: string): Topic {
  return { id, project_id: 'p1', title, kind: 'channel', status: 'active' } as Topic
}

function listed(): Topic[] {
  return queryClient.getQueryData<{ data: Topic[] }>(keys.projectTopics('p1'))?.data ?? []
}

afterEach(() => {
  getTopic.mockReset()
  listTopics.mockReset()
})

describe('房间这一行变了', () => {
  it('指名了哪一行就只重取那一行，补进清单，不重下整份清单', async () => {
    queryClient.setQueryData(keys.projectTopics('p1'), { data: [topic('a', '旧名'), topic('b', '别的')], total: 2 })
    getTopic.mockResolvedValue(topic('a', '新名'))
    await roomChanged({ room: 'a', project: 'p1', resource: 'topics', id: 'a' })
    expect(listed().map((row) => row.title)).toEqual(['新名', '别的'])
    expect(listTopics).not.toHaveBeenCalled()
  })

  it('清单里没有这一行（刚变得看得见的私有频道）：整份清单作废', async () => {
    queryClient.setQueryData(keys.projectTopics('p1'), { data: [topic('b', '别的')], total: 1 })
    getTopic.mockResolvedValue(topic('a', '私有'))
    await roomChanged({ room: 'a', project: 'p1', resource: 'topics', id: 'a' })
    expect(queryClient.getQueryState(keys.projectTopics('p1'))?.isInvalidated).toBe(true)
  })

  it('那一行在读的时候我改了它的名字：读回来的改名之前的样子不补进去', async () => {
    setActivePinia(createPinia())
    seedProjects([])
    seedProject('p1', { topics: [topic('a', '旧名')], members: [], notifyLevels: {} })
    const store = useWorkspaceStore()
    store.openProject('p1')
    let answer!: (row: Topic) => void
    getTopic.mockReturnValue(new Promise<Topic>((resolve) => (answer = resolve)))
    const pending = roomChanged({ room: 'a', project: 'p1', resource: 'topics', id: 'a' })
    setTopicTitle.mockResolvedValue(topic('a', '我改的'))
    await store.renameTopic('a', '我改的')
    answer(topic('a', '旧名'))
    await pending
    expect(store.topics.map((row) => row.title)).toEqual(['我改的'])
  })
})

describe('任务变了', () => {
  it('房间和项目的任务清单、每一件任务本身都作废', async () => {
    queryClient.setQueryData(keys.roomTaskList('a', { status: 'open' }), { data: [], total: 0 })
    queryClient.setQueryData(keys.projectOpenTasks('p1'), { data: [], total: 0 })
    queryClient.setQueryData(keys.roomTask('t1'), { id: 't1' })
    await roomChanged({ room: 'a', project: 'p1', resource: 'tasks' })
    for (const key of [keys.roomTaskList('a', { status: 'open' }), keys.projectOpenTasks('p1'), keys.roomTask('t1')]) {
      expect(queryClient.getQueryState(key)?.isInvalidated).toBe(true)
    }
  })
})
