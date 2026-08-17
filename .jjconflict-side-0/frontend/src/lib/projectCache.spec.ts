import type { Project } from '@/cx_types'

import { beforeEach, describe, expect, it } from 'vitest'

import { loadCachedProjects, saveCachedProjects } from './projectCache'

const projects = [{ id: 'p1', name: 'Project One' }] as Project[]

describe('project rail cache', () => {
  beforeEach(() => sessionStorage.clear())

  it('restores a recent list for the same user', () => {
    saveCachedProjects('alice', projects, 1_000)
    expect(loadCachedProjects('alice', 2_000)).toEqual(projects)
  })

  it('does not expose one user project list to another user', () => {
    saveCachedProjects('alice', projects, 1_000)
    expect(loadCachedProjects('bob', 2_000)).toEqual([])
    expect(loadCachedProjects('', 2_000)).toEqual([])
  })

  it('drops expired or malformed data', () => {
    saveCachedProjects('alice', projects, 1_000)
    expect(loadCachedProjects('alice', 25 * 60 * 60 * 1000)).toEqual([])
    sessionStorage.setItem('cheesex.projects.v1:alice', '{broken')
    expect(loadCachedProjects('alice', 2_000)).toEqual([])
  })
})
