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

const { queryClient } = await import('@/query/client')
const { keys } = await import('@/query/keys')
const { openRoomTasksQuery, pinsQuery, roomMembersQuery, roomTaskQuery, roomTasksQuery } = await import('@/query/room')
const { RECENT_DONE } = await import('@/lib/channelTasks')
const { expectRoom, fromSnapshot, settleRoom } = await import('@/query/snapshot')

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
    expectRoom('task', 'room')
    const row = queryClient.fetchQuery(roomTaskQuery('task'))
    const related = fromSnapshot(keys.taskRelated('task'), () => Promise.reject(new Error('asked')))
    settleRoom('task', 'room', {
      members: [member('alice')],
      feedback_proposals: [],
      skill_proposals: [],
      task: { id: 'task', title: '整理周报' },
      related: { origin: null, materials: [] },
      review_comments: { comments: [] },
    } as unknown as RoomSnapshot)

    expect((await row).id).toBe('task')
    expect(await related).toEqual({ origin: null, materials: [] })
    expect(queryClient.getQueryData<{ data: unknown[] }>(keys.roomMembers('room'))?.data).toHaveLength(1)
    expect(api.getTask).not.toHaveBeenCalled()
  })
})
