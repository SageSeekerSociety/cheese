import type { RouteLocationNormalizedLoaded } from 'vue-router'

import { beforeEach, describe, expect, it } from 'vitest'

import { forgetEntry, projectFrameOf, readEntry, recordEntry, writeEntry } from './projectEntry'

const TEAM = '小队'

/** 一条已经解析好的路由，只带这套逻辑真正读到的那几样。 */
function at(
  name: string,
  { project, params = {}, title = '' }: { project?: string; params?: Record<string, string>; title?: string } = {}
): RouteLocationNormalizedLoaded {
  const matched = [
    ...(title ? [{ meta: { title } }] : []),
    ...(project ? [{ meta: { projectFrame: true } }] : []),
    { meta: {} },
  ]
  return {
    name,
    params: { ...(project ? { projectId: project } : {}), ...params },
    matched,
  } as unknown as RouteLocationNormalizedLoaded
}

const labelOf = (route: RouteLocationNormalizedLoaded) => {
  for (const record of [...route.matched].reverse()) {
    if (record.meta?.title) return String(record.meta.title)
  }
  return ''
}

describe('项目的来路', () => {
  beforeEach(() => sessionStorage.clear())

  it('从项目外面走进来，记下来路', () => {
    recordEntry(
      at('workspace-project', { project: 'p1' }),
      at('TeamsDetail', { params: { teamId: '7' }, title: TEAM }),
      labelOf
    )
    expect(readEntry('p1')).toEqual({ name: 'TeamsDetail', params: { teamId: '7' }, label: TEAM })
  })

  // 从话题按 ← 回到话题列表也是一次跳转。它要是覆盖了入口，← 就指回你刚离开的
  // 那条话题——按一下原地弹回去，再也出不了这个项目。
  it('项目里怎么翻都不覆盖来路', () => {
    const team = at('TeamsDetail', { params: { teamId: '7' }, title: TEAM })
    recordEntry(at('workspace-project', { project: 'p1' }), team, labelOf)
    recordEntry(
      at('workspace-topic', { project: 'p1', params: { topicId: 't1' } }),
      at('workspace-project', { project: 'p1' }),
      labelOf
    )
    recordEntry(
      at('workspace-project', { project: 'p1' }),
      at('workspace-topic', { project: 'p1', params: { topicId: 't1' } }),
      labelOf
    )
    expect(readEntry('p1')?.name).toBe('TeamsDetail')
  })

  // 左栏切项目是同一层上的平移，不是「从上一层走进来」。记了的话，B 项目的 ←
  // 会指向 A 项目——一个它从来没有过的上一层。
  it('项目之间横切不算走进来', () => {
    recordEntry(at('workspace-project', { project: 'p2' }), at('workspace-topic', { project: 'p1' }), labelOf)
    expect(readEntry('p2')).toBeNull()
  })

  // 刷新和贴链接直接打开，from 是初始位置：没人待过那儿，指过去等于把人踢出这个
  // 应用。这正是当初不敢用 history.back() 的那件事。
  it('没有来路的进入（刷新 / 贴链接）不记', () => {
    recordEntry(
      at('workspace-project', { project: 'p1' }),
      { name: undefined, params: {}, matched: [] } as unknown as RouteLocationNormalizedLoaded,
      labelOf
    )
    expect(readEntry('p1')).toBeNull()
  })

  it('每个项目各记各的', () => {
    recordEntry(
      at('workspace-project', { project: 'p1' }),
      at('TeamsDetail', { params: { teamId: '7' }, title: TEAM }),
      labelOf
    )
    expect(readEntry('p2')).toBeNull()
  })

  it('目的地不在项目框里就不记', () => {
    recordEntry(at('TeamsDetail', { params: { teamId: '7' } }), at('HomeSpaces', { title: '空间' }), labelOf)
    expect(sessionStorage.length).toBe(0)
  })

  it('存坏了当作没有，而不是让顶栏炸掉', () => {
    sessionStorage.setItem('cheese:project-entry:p1', '{ not json')
    expect(readEntry('p1')).toBeNull()
  })

  it('忘掉一个项目的来路', () => {
    writeEntry('p1', { name: 'TeamsDetail', params: { teamId: '7' }, label: TEAM })
    forgetEntry('p1')
    expect(readEntry('p1')).toBeNull()
  })

  describe('哪条路由算在项目框里', () => {
    it('框里的层认得出自己属于哪个项目', () => {
      expect(projectFrameOf(at('workspace-topic', { project: 'p1' }))).toBe('p1')
    })

    it('框外的层不属于任何项目', () => {
      expect(projectFrameOf(at('TeamsDetail', { params: { teamId: '7' } }))).toBeNull()
    })
  })
})
