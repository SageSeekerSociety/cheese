// 项目工作区按屏幕宽度分三种摆法：手机上列表和房间各占一整页，平板上并排成两栏，
// 桌面上是常驻侧栏。两栏只在列表和房间那两层：总览、文档、设置在平板上仍是一整页。
import { describe, expect, it } from 'vitest'

import { COMPACT_DESKTOP_MAX_WIDTH, compactDesktop, showsTopicList, workspaceLayout } from '../useWorkspaceLayout'

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

describe('compactDesktop', () => {
  // 「桌面窄档」= 960（mdAndUp 那条线）到这个宽度为止（含）。这一档里常驻侧栏挤得
  // 正文只剩一条，所以它改成可收起的浮层，话题页也只画对话。
  const desktop = (width: number) => compactDesktop(width, true)

  it('960 到 1180 是桌面窄档', () => {
    expect(desktop(960)).toBe(true)
    expect(desktop(1024)).toBe(true)
    expect(desktop(1180)).toBe(true)
    expect(desktop(COMPACT_DESKTOP_MAX_WIDTH)).toBe(true)
  })

  it('比 1180 宽就回到今天的样子', () => {
    expect(desktop(1181)).toBe(false)
    expect(desktop(1280)).toBe(false)
    expect(desktop(1920)).toBe(false)
  })

  it('不是桌面（手机 / 平板竖屏）就不是窄档', () => {
    expect(compactDesktop(1024, false)).toBe(false)
    expect(compactDesktop(390, false)).toBe(false)
  })

  it('宽度量不出来时按宽档算，不额外加一层', () => {
    // 只给了 mdAndUp 的替身（不少用例这样模拟 vuetify）量不出 width。
    expect(compactDesktop(0, true)).toBe(false)
  })
})

describe('showsTopicList', () => {
  it('列表和房间两层留着左边的列表', () => {
    expect(showsTopicList('workspace-project')).toBe(true)
    expect(showsTopicList('workspace-topic')).toBe(true)
  })

  it('项目的其余各页仍是一整页', () => {
    for (const name of ['workspace-overview', 'project-docs', 'project-settings', 'project-members', 'workspace-dm'])
      expect(showsTopicList(name)).toBe(false)
  })
})
