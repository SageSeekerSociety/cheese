// 项目工作区按屏幕宽度分三种摆法：手机上列表和房间各占一整页，平板上并排成两栏，
// 桌面上是常驻侧栏。两栏只在列表和房间那两层：看板、文档、设置在平板上仍是一整页。
import { describe, expect, it } from 'vitest'

import { showsTopicList, workspaceLayout } from '../useWorkspaceLayout'

// 桌面由 Vuetify 的 mdAndUp 说了算（≥ 960），这里按同一条线传进去。
const at = (width: number) => workspaceLayout(width, width >= 960)

describe('workspaceLayout', () => {
  it('手机竖屏是一页一页走的手机摆法', () => {
    expect(at(360)).toBe('phone')
    expect(at(390)).toBe('phone')
    expect(at(767)).toBe('phone')
  })

  it('平板竖屏和横过来的大手机把列表和房间并排', () => {
    expect(at(768)).toBe('split')
    expect(at(820)).toBe('split')
    expect(at(959)).toBe('split')
  })

  it('桌面宽度不变', () => {
    expect(at(960)).toBe('desktop')
    expect(at(1280)).toBe('desktop')
  })
})

describe('showsTopicList', () => {
  it('列表和房间两层留着左边的列表', () => {
    expect(showsTopicList('workspace-project')).toBe(true)
    expect(showsTopicList('workspace-topic')).toBe(true)
  })

  it('项目的其余各页仍是一整页', () => {
    for (const name of ['workspace-running', 'project-docs', 'project-settings', 'project-members', 'workspace-dm'])
      expect(showsTopicList(name)).toBe(false)
  })
})
