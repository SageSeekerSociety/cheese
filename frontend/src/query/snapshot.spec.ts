// 进房间时，名册、任务、置顶这些随订阅的 `subscribed` 帧一起来（`room`），不再各发一个请求。
import type { RoomSnapshot } from '@/query/snapshot'

import { afterEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listTopicMembers: vi.fn(),
  listPins: vi.fn(),
  getTask: vi.fn(),
  listRoomTasks: vi.fn(),
}))
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  listTopicMembers: api.listTopicMembers,
  getTask: api.getTask,
  listRoomTasks: api.listRoomTasks,
}))
vi.mock('@/api/pins', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/pins')>()),
  listPins: api.listPins,
}))
vi.mock('@/api/chapters', () => ({
  listChapters: async () => [],
  addChapter: vi.fn(),
  removeChapter: vi.fn(),
  renameChapter: vi.fn(),
}))

const { queryClient } = await import('@/query/client')
const { keys } = await import('@/query/keys')
const { openRoomTasksQuery, pinsQuery, roomMembersQuery, roomTaskQuery, roomTasksQuery } = await import('@/query/room')
const { RECENT_DONE } = await import('@/lib/channelTasks')
const { expectRoom, fromSnapshot, readEarly, settleRoom } = await import('@/query/snapshot')

const member = (handle: string) => ({ topic_id: 'room', member_handle: handle, role: 'member' })

function channel(members: string[], pins: unknown[] = []): RoomSnapshot {
  return {
    members: members.map(member),
    feedback_proposals: [],
    skill_proposals: [],
    tasks: { open: [], recent: [] },
    pins,
    threads: [],
  } as unknown as RoomSnapshot
}

afterEach(() => {
  vi.useRealTimers()
  for (const fn of Object.values(api)) fn.mockReset()
})

describe('进一个频道', () => {
  it('页面先要的那几样，等快照来了就用快照里的，不另发请求', async () => {
    expectRoom('room', 'room')
    const roster = queryClient.fetchQuery(roomMembersQuery('room'))
    const pins = queryClient.fetchQuery(pinsQuery('room'))
    settleRoom('room', 'room', channel(['alice', 'bob'], [{ pinned_by: 'alice' }]))

    expect((await roster).data.map((row) => row.member_handle)).toEqual(['alice', 'bob'])
    expect(await pins).toEqual([{ pinned_by: 'alice' }])
    expect(api.listTopicMembers).not.toHaveBeenCalled()
    expect(api.listPins).not.toHaveBeenCalled()
  })

  it('频道概览要的进行中和最近做完的任务也在快照里', async () => {
    expectRoom('room', 'room')
    const open = queryClient.fetchQuery(openRoomTasksQuery('room'))
    const recent = queryClient.fetchQuery(roomTasksQuery('room', { limit: 0, status: 'closed', latest: RECENT_DONE }))
    settleRoom('room', 'room', {
      ...channel(['alice']),
      tasks: { open: [{ id: 'k1' }], recent: [{ id: 'k0' }] },
    } as unknown as RoomSnapshot)

    expect((await open).data.map((task) => task.id)).toEqual(['k1'])
    expect((await recent).data.map((task) => task.id)).toEqual(['k0'])
    expect(api.listRoomTasks).not.toHaveBeenCalled()
  })

  it('订阅没带快照（没读成）：各自去问', async () => {
    api.listTopicMembers.mockResolvedValue({ data: [member('alice')], total: 1 })
    expectRoom('room', 'room')
    const roster = queryClient.fetchQuery(roomMembersQuery('room'))
    settleRoom('room', 'room', null)

    expect((await roster).data.map((row) => row.member_handle)).toEqual(['alice'])
    expect(api.listTopicMembers).toHaveBeenCalledTimes(1)
  })

  it('快照迟迟不来：不让房间一直空着，过一会儿各自去问', async () => {
    vi.useFakeTimers()
    api.listTopicMembers.mockResolvedValue({ data: [member('alice')], total: 1 })
    expectRoom('room', 'room')
    const roster = queryClient.fetchQuery(roomMembersQuery('room'))
    await vi.advanceTimersByTimeAsync(3_000)

    expect((await roster).data.map((row) => row.member_handle)).toEqual(['alice'])
  })

  it('快照之后才发起的读（推送说变了）照常问服务器', async () => {
    expectRoom('room', 'room')
    settleRoom('room', 'room', channel(['alice']))
    api.listTopicMembers.mockResolvedValue({ data: [member('alice'), member('carol')], total: 2 })

    await queryClient.invalidateQueries({ queryKey: keys.roomMembers('room') })
    const roster = await queryClient.fetchQuery({ ...roomMembersQuery('room'), staleTime: 0 })

    expect(roster.data.map((row) => row.member_handle)).toEqual(['alice', 'carol'])
  })

  it('断线又连上：新的快照直接换掉手上那一份', async () => {
    settleRoom('room', 'room', channel(['alice']))
    settleRoom('room', 'room', channel(['alice', 'dave']))

    const held = queryClient.getQueryData<{ data: { member_handle: string }[] }>(keys.roomMembers('room'))
    expect(held?.data.map((row) => row.member_handle)).toEqual(['alice', 'dave'])
  })
})

describe('进一个任务', () => {
  it('任务自己、相关都在快照里；名册是它所在频道的', async () => {
    // 从任务清单点进来：这一件手上已经有了，任务页先画它，对话栏挂得上，订阅连得上。
    queryClient.setQueryData(keys.roomTaskList('room', { limit: 0, status: 'open' }), {
      data: [{ id: 'task', title: '整理周报' }],
      total: 1,
    })
    expectRoom('task', 'room')
    const row = queryClient.fetchQuery(roomTaskQuery('task'))
    const related = fromSnapshot(keys.taskRelated('task'), () => Promise.reject(new Error('asked')))
    // 技能提议卡跟着提出它的那段对话走：任务页按任务去要，快照里那一格就是任务的。
    const skills = fromSnapshot(keys.roomSkillProposals('task'), () => Promise.reject(new Error('asked')))
    const roomSkills = keys.roomSkillProposals('room')
    settleRoom('task', 'room', {
      members: [member('alice')],
      feedback_proposals: [],
      skill_proposals: [{ id: 'sk-1', source_topic_id: 'task' }],
      task: { id: 'task', title: '整理周报' },
      related: { origin: null, materials: [] },
      review_comments: { comments: [] },
    } as unknown as RoomSnapshot)

    expect((await row).id).toBe('task')
    expect(await related).toEqual({ origin: null, materials: [] })
    expect(await skills).toEqual([{ id: 'sk-1', source_topic_id: 'task' }])
    expect(queryClient.getQueryData(roomSkills)).toBeUndefined()
    expect(queryClient.getQueryData<{ data: unknown[] }>(keys.roomMembers('room'))?.data).toHaveLength(1)
    expect(api.getTask).not.toHaveBeenCalled()
  })
})

describe('冷打开', () => {
  it('先发出去的那一次读，页面挂上来的读直接接着它，不再问', async () => {
    const later = vi.fn().mockResolvedValue(['later'])
    readEarly(keys.roomFeedbackProposals('cold'), async () => ['early'])

    expect(await fromSnapshot(keys.roomFeedbackProposals('cold'), later)).toEqual(['early'])
    expect(later).not.toHaveBeenCalled()
  })

  it('接手过一次之后，再读（推送说变了）照常问服务器', async () => {
    readEarly(keys.roomFeedbackProposals('cold2'), async () => ['early'])
    await fromSnapshot(keys.roomFeedbackProposals('cold2'), async () => [])

    expect(await fromSnapshot(keys.roomFeedbackProposals('cold2'), async () => ['fresh'])).toEqual(['fresh'])
  })
})

describe('冷打开一件任务', () => {
  // 手上没有这一件：任务页要等它才挂得上对话栏，带快照的订阅又要等对话栏挂上才发。等快照
  // 就是干等到超时，所以直接问。
  it('手上没有这一件，不等快照，马上去问', async () => {
    vi.useFakeTimers()
    api.getTask.mockResolvedValue({ id: 'cold-task', title: '整理周报' })
    expectRoom('cold-task', 'room')
    const row = queryClient.fetchQuery(roomTaskQuery('cold-task'))
    await vi.advanceTimersByTimeAsync(50)

    expect(api.getTask).toHaveBeenCalledWith('cold-task')
    expect((await row).id).toBe('cold-task')
  })
})
