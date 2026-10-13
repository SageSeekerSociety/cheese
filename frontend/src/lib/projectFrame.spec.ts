import type { RouteLocationNormalizedLoaded } from 'vue-router'

import { describe, expect, it } from 'vitest'

import { projectFrameOf } from './projectFrame'

/** 一条已经解析好的路由，只带这套逻辑真正读到的那几样。 */
function at(
  name: string,
  { project, params = {} }: { project?: string; params?: Record<string, string> } = {}
): RouteLocationNormalizedLoaded {
  const matched = [...(project ? [{ meta: { projectFrame: true } }] : []), { meta: {} }]
  return {
    name,
    params: { ...(project ? { projectId: project } : {}), ...params },
    matched,
  } as unknown as RouteLocationNormalizedLoaded
}

describe('哪条路由算在项目框里', () => {
  it('框里的层认得出自己属于哪个项目', () => {
    expect(projectFrameOf(at('workspace-topic', { project: 'p1' }))).toBe('p1')
  })

  it('框外的层不属于任何项目', () => {
    expect(projectFrameOf(at('TeamsDetailDefault', { params: { handle: 'zhishi' } }))).toBeNull()
  })
})
