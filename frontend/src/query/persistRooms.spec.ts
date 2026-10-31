// 存进浏览器的房间缓存：存哪几份、放回给谁。放回的判据是「这次启动问到的项目清单里
// 还有这个项目」，换了人、存得太久的都不放。
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice', myId: () => '7' }))

import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { collectRooms, MAX_ROOMS, restoreRooms } from '@/query/persistRooms'

const P1 = 'p-1'
const P2 = 'p-2'

function block(id: number) {
  return { id: `b${id}` }
}

function seed(): void {
  queryClient.setQueryData(keys.projectTopics(P1), { data: [{ id: 'r-1' }, { id: 'r-2' }] })
  queryClient.setQueryData(keys.projectMembers(P1), [{ handle: 'alice' }])
  queryClient.setQueryData(keys.roomRow('r-3'), { id: 'r-3', project_id: P2 })
  queryClient.setQueryData(keys.roomNewestBlocks('r-1'), { blocks: [block(1)], hasMore: false })
  queryClient.setQueryData(keys.roomMembers('r-1'), [{ handle: 'alice' }])
  queryClient.setQueryData(keys.roomNewestBlocks('r-3'), {
    blocks: Array.from({ length: 80 }, (_, i) => block(i)),
    hasMore: false,
  })
  // 认不出归哪个项目的房间不存。
  queryClient.setQueryData(keys.roomNewestBlocks('r-x'), { blocks: [block(9)], hasMore: false })
  // 不在存的范围里的不存。
  queryClient.setQueryData(['project', P1, 'settings'], { secret: 1 })
}

function hashes(projects: ReturnType<typeof collectRooms>, pid: string): string[] {
  return (projects[pid]?.queries ?? []).map((q) => q.queryHash).sort()
}

describe('persisted rooms', () => {
  beforeEach(() => {
    queryClient.clear()
  })

  it('按项目分包存频道清单、成员和最近房间的最新一页，往上翻过的只留最后一页', () => {
    seed()
    const projects = collectRooms()

    expect(Object.keys(projects).sort()).toEqual([P1, P2])
    expect(hashes(projects, P1)).toEqual(
      [keys.projectMembers(P1), keys.projectTopics(P1), keys.roomMembers('r-1'), keys.roomNewestBlocks('r-1')]
        .map((k) => JSON.stringify(k))
        .sort()
    )
    const window = projects[P2].queries.find((q) => q.queryHash === JSON.stringify(keys.roomNewestBlocks('r-3')))!
    const data = window.state.data as { blocks: { id: string }[]; hasMore: boolean }
    expect(data.blocks).toHaveLength(50)
    expect(data.blocks.at(-1)!.id).toBe('b79')
    expect(data.hasMore).toBe(true)
    // 缓存里那一份没被截。
    expect(queryClient.getQueryData<{ blocks: unknown[] }>(keys.roomNewestBlocks('r-3'))!.blocks).toHaveLength(80)
    const all = Object.values(projects).flatMap((b) => b.queries.map((q) => q.queryHash))
    expect(all).not.toContain(JSON.stringify(keys.roomNewestBlocks('r-x')))
    expect(all).not.toContain(JSON.stringify(['project', P1, 'settings']))
  })

  it(`最多存最近读过的 ${MAX_ROOMS} 个房间`, () => {
    const ids = Array.from({ length: MAX_ROOMS + 5 }, (_, i) => `r-${i}`)
    queryClient.setQueryData(keys.projectTopics(P1), { data: ids.map((id) => ({ id })) })
    ids.forEach((id, i) =>
      queryClient.setQueryData(keys.roomNewestBlocks(id), { blocks: [block(i)], hasMore: false }, { updatedAt: i + 1 })
    )
    const rooms = collectRooms()
      [P1].queries.filter((q) => q.queryKey[0] === 'room')
      .map((q) => q.queryKey[1])

    expect(rooms).toHaveLength(MAX_ROOMS)
    expect(rooms).not.toContain('r-0')
    expect(rooms).toContain(`r-${MAX_ROOMS + 4}`)
  })

  it('只放回这次项目清单里还在的项目', () => {
    seed()
    const saved = { handle: 'alice', savedAt: Date.now(), projects: JSON.parse(JSON.stringify(collectRooms())) }
    queryClient.clear()

    restoreRooms(saved, 'alice', new Set([P1]))

    expect(queryClient.getQueryData(keys.roomNewestBlocks('r-1'))).toEqual({ blocks: [block(1)], hasMore: false })
    expect(queryClient.getQueryData(keys.projectTopics(P1))).toBeDefined()
    expect(queryClient.getQueryData(keys.roomNewestBlocks('r-3'))).toBeUndefined()
    expect(queryClient.getQueryData(keys.roomRow('r-3'))).toBeUndefined()
  })

  it('换了人、存得太久的不放回', () => {
    seed()
    const projects = JSON.parse(JSON.stringify(collectRooms()))
    queryClient.clear()

    expect(restoreRooms({ handle: 'bob', savedAt: Date.now(), projects }, 'alice', new Set([P1, P2]))).toBe(0)
    expect(
      restoreRooms({ handle: 'alice', savedAt: Date.now() - 8 * 24 * 3600 * 1000, projects }, 'alice', new Set([P1]))
    ).toBe(0)
    expect(queryClient.getQueryData(keys.projectTopics(P1))).toBeUndefined()
  })

  it('已经问回来的新数据不被放回的旧数据盖掉', () => {
    seed()
    const projects = JSON.parse(JSON.stringify(collectRooms()))
    queryClient.clear()
    queryClient.setQueryData(
      keys.roomNewestBlocks('r-1'),
      { blocks: [block(2)], hasMore: false },
      { updatedAt: Date.now() + 1000 }
    )

    restoreRooms({ handle: 'alice', savedAt: Date.now(), projects }, 'alice', new Set([P1]))

    expect(queryClient.getQueryData(keys.roomNewestBlocks('r-1'))).toEqual({ blocks: [block(2)], hasMore: false })
  })
})
