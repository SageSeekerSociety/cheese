/** 侧栏拿到的那棵树：房间 + 房间里派出去的活。
 *
 * 这一层单独钉，是因为它有个容易反过来做的地方：支线**不能**并进 `topics`。
 * 那一份是「房间」，@话题 补全和文档里的 <#id> 解析都读它——把活混进去会顺带改掉
 * 那些地方的含义，而屏幕上一时看不出来。所以树是第三个东西，由两份合出来。
 */
import type { RoomTask, Topic } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listTopics = vi.fn()
const listProjectTasks = vi.fn()
const getPlace = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listTopics: (...a: unknown[]) => listTopics(...a),
    listProjectTasks: (...a: unknown[]) => listProjectTasks(...a),
    getPlace: (...a: unknown[]) => getPlace(...a),
    listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
    getTopicUnread: vi.fn().mockResolvedValue({}),
    getPrivateUnread: vi.fn().mockResolvedValue({}),
    listProjects: vi.fn().mockResolvedValue({ data: [] }),
  }
})

import { useWorkspaceStore } from '../workspace'

const ROOM: Topic = {
  id: 'room-1',
  project_id: 'p1',
  parent_id: null,
  title: '运维',
  kind: 'topic',
  status: 'active',
  created_at: '2026-08-23T00:00:00Z',
}

const THREAD: RoomTask = {
  id: 'thread-1',
  project_id: 'p1',
  room_id: 'room-1',
  title: '查一下分页接口',
  status: 'open',
  owner_handle: 'cheese',
  created_at: '2026-08-23T01:00:00Z',
  updated_at: '2026-08-23T01:00:00Z',
  card: { id: 'c1', status: 'pr_open', pr_number: 611, pr_url: 'https://x/611' },
  // 落哪一列、写哪句话，全由后端给。这一份用例不关心是哪一列，但字段必须在。
  presentation: { column: 'building', display_status: '运行中' },
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  listTopics.mockResolvedValue({ data: [ROOM] })
  listProjectTasks.mockResolvedValue({ data: [THREAD] })
})

async function opened() {
  const store = useWorkspaceStore()
  await store.openProject('p1')
  await store.refreshProjectTasks()
  return store
}

describe('侧栏那棵树', () => {
  it('房间和它派出去的活都在里面', async () => {
    const store = await opened()
    expect(store.tree.map((t) => t.id).sort()).toEqual(['room-1', 'thread-1'])
  })

  it('一件活挂在它所在的房间下面 —— 树就是靠这条边建出来的', async () => {
    const store = await opened()
    const thread = store.tree.find((t) => t.id === 'thread-1')
    expect(thread?.parent_id).toBe('room-1')
    expect(thread?.kind).toBe('thread')
  })

  it('它骑的那张卡跟着一起过来', async () => {
    const store = await opened()
    expect(store.tree.find((t) => t.id === 'thread-1')?.card?.pr_number).toBe(611)
  })

  it('`topics` 仍然只有房间 —— @话题 补全和 <#id> 解析读的是那一份', async () => {
    const store = await opened()
    expect(store.topics.map((t) => t.id)).toEqual(['room-1'])
  })

  it('一次问全，不是一个房间一个请求', async () => {
    await opened()
    expect(listProjectTasks).toHaveBeenCalledWith('p1')
  })

  it('换项目要把上一个项目的活丢掉，不能串台', async () => {
    const store = await opened()
    expect(store.tree).toHaveLength(2)

    listTopics.mockResolvedValue({ data: [] })
    listProjectTasks.mockResolvedValue({ data: [] })
    await store.openProject('p2')

    expect(store.tree.some((t) => t.id === 'thread-1')).toBe(false)
  })
})
