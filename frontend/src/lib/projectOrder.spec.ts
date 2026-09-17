import type { Project } from '@/cx_types'

import { beforeEach, describe, expect, it } from 'vitest'

import { applyProjectOrder, loadProjectOrder, reorderProjects, saveProjectOrder } from './projectOrder'

const project = (id: string): Project => ({ id, name: id.toUpperCase() }) as Project
const names = (projects: Project[]) => projects.map((p) => p.id)

// 服务端给的是 created_at desc，所以清单头上的是最新的那个。
const listed = [project('c'), project('b'), project('a')]

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
