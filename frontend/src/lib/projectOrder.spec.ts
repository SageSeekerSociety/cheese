import type { Project } from '@/cx_types'

import { beforeEach, describe, expect, it } from 'vitest'

import { applyProjectOrder, dropTargetAt, loadProjectOrder, reorderProjects, saveProjectOrder } from './projectOrder'

const project = (id: string): Project => ({ id, name: id.toUpperCase() }) as Project
const names = (projects: Project[]) => projects.map((p) => p.id)

// 服务端给的是 created_at desc，所以清单头上的是最新的那个。
const listed = [project('c'), project('b'), project('a')]

// 一列三格，各高 40，相邻之间留 8 的缝：a=[100,140] b=[148,188] c=[196,236]。
const column = [
  { id: 'a', top: 100, bottom: 140 },
  { id: 'b', top: 148, bottom: 188 },
  { id: 'c', top: 196, bottom: 236 },
]

describe('rail 这一列里，某个纵坐标落在哪儿', () => {
  it('落在一格身上，按上下半边分', () => {
    expect(dropTargetAt(column, 110)).toEqual({ id: 'a', edge: 'before' })
    expect(dropTargetAt(column, 135)).toEqual({ id: 'a', edge: 'after' })
    expect(dropTargetAt(column, 230)).toEqual({ id: 'c', edge: 'after' })
  })

  // 这一条就是那个 bug：第一格上方本来没人负责，插入线画着「插到最前」，松手却
  // 什么都不发生，而想把一个项目挪到最前面，手势天然会往上多走一点。
  it('第一格上方的留白算插到最前', () => {
    expect(dropTargetAt(column, 99)).toEqual({ id: 'a', edge: 'before' })
    expect(dropTargetAt(column, 0)).toEqual({ id: 'a', edge: 'before' })
    expect(dropTargetAt(column, -50)).toEqual({ id: 'a', edge: 'before' })
  })

  it('最后一格下方的留白算插到最末', () => {
    expect(dropTargetAt(column, 237)).toEqual({ id: 'c', edge: 'after' })
    expect(dropTargetAt(column, 9999)).toEqual({ id: 'c', edge: 'after' })
  })

  // 缝里读成「上面那一格的后面」，和读成「下面那一格的前面」是同一个位置。
  it('两格之间的缝落在上面那一格的后面', () => {
    expect(dropTargetAt(column, 144)).toEqual({ id: 'a', edge: 'after' })
  })

  it('一个格子都没有时不认领', () => {
    expect(dropTargetAt([], 120)).toBeNull()
  })
})

describe('project rail order', () => {
  beforeEach(() => localStorage.clear())

  it('keeps one user order out of another user rail', () => {
    saveProjectOrder('alice', ['a', 'b'])
    expect(loadProjectOrder('bob')).toEqual([])
    expect(loadProjectOrder('')).toEqual([])
  })

  it('survives a malformed stored order', () => {
    localStorage.setItem('cheesex.projectOrder.v1:alice', '{broken')
    expect(loadProjectOrder('alice')).toEqual([])
  })

  it('leaves the rail as the server sent it when nothing was dragged', () => {
    expect(names(applyProjectOrder(listed, []))).toEqual(['c', 'b', 'a'])
  })

  it('draws the dragged order', () => {
    expect(names(applyProjectOrder(listed, ['a', 'c', 'b']))).toEqual(['a', 'c', 'b'])
  })

  it('puts a project created after the drag back on top', () => {
    const withNewest = [project('d'), ...listed]
    expect(names(applyProjectOrder(withNewest, ['a', 'c', 'b']))).toEqual(['d', 'a', 'c', 'b'])
  })

  it('forgets a project that is no longer listed', () => {
    expect(names(applyProjectOrder([project('b'), project('a')], ['a', 'gone', 'b']))).toEqual(['a', 'b'])
  })

  it('drops before and after the target, so both ends are reachable', () => {
    expect(reorderProjects(listed, 'a', 'c', 'before')).toEqual(['a', 'c', 'b'])
    expect(reorderProjects(listed, 'c', 'a', 'after')).toEqual(['b', 'a', 'c'])
  })

  it('reports the whole order, not just the tile that moved', () => {
    expect(reorderProjects(listed, 'b', 'c', 'before')).toEqual(['b', 'c', 'a'])
  })

  it('is a no-op when the tile is dropped on itself or on a stranger', () => {
    expect(reorderProjects(listed, 'b', 'b', 'before')).toEqual(['c', 'b', 'a'])
    expect(reorderProjects(listed, 'b', 'zzz', 'after')).toEqual(['c', 'b', 'a'])
  })
})
