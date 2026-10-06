import type { Project } from '@/cx_types'

import { describe, expect, it } from 'vitest'

import { DEFAULT_SHELL, orderedNav, projectPageLayout, shellFor, shellOf, termParams } from './shell'

import { t } from '@/i18n'

// 这一版前端还认得的那四格（`TopicSidebar.vue` 的 PROJECT_PAGES）。壳只决定
// 「露哪几格」，认不认得某一格是前端的事。
//
// 它比加壳那天短了：main 的 #1330/#1339 把总览与导出与发布并进了首页，AI 队友
// 也不再是一页，于是那三个 key 从这里消失，default 壳的声明跟着一起收窄。
const KNOWN = ['workspace-running', 'project-routines', 'project-library', 'project-members']

function project(id: string, shell?: unknown): Project {
  return { id, name: id, created_at: '', ...(shell ? { shell } : {}) } as Project
}

function shellLike(over: Partial<typeof DEFAULT_SHELL> = {}) {
  return { ...DEFAULT_SHELL, ...over }
}

describe('shellFor: 「还不知道」和「知道，就是 default」是两件事', () => {
  it('这个项目不在清单里时是 null，不是 default', () => {
    // 清单没到货、或者这个项目根本不是我的 —— 两种都得答「不知道」。答 default
    // 会让人在还没读到壳的时候就把第一屏定死。
    expect(shellFor([project('a')], 'b')).toBeNull()
    expect(shellFor([], 'a')).toBeNull()
    expect(shellFor(undefined, 'a')).toBeNull()
  })

  it('没有项目 id 时也是 null', () => {
    expect(shellFor([project('a')], null)).toBeNull()
    expect(shellFor([project('a')], undefined)).toBeNull()
  })

  it('清单里有、它没声明壳 —— 这才是 default', () => {
    expect(shellFor([project('a')], 'a')).toBe(DEFAULT_SHELL)
  })

  it('项目自己声明了壳就用它，不是 default', () => {
    const declared = shellLike({ name: 'declared' })
    expect(shellFor([project('a', declared)], 'a')).toBe(declared)
  })

  it('shellOf 直接拿一个项目，没有就是 default', () => {
    expect(shellOf(null)).toBe(DEFAULT_SHELL)
    expect(shellOf(undefined)).toBe(DEFAULT_SHELL)
    expect(shellOf(project('a'))).toBe(DEFAULT_SHELL)
  })
})

describe('orderedNav: 顺序听壳的，认不认得听前端的', () => {
  it('按壳给的顺序画', () => {
    const shell = shellLike({ nav: { ...DEFAULT_SHELL.nav, tabs: ['inbox', 'home', 'workspace'] } })
    expect(orderedNav(shell, 'tabs', ['home', 'workspace', 'inbox'])).toEqual(['inbox', 'home', 'workspace'])
  })

  it('壳没列的格子不画', () => {
    const shell = shellLike({ nav: { ...DEFAULT_SHELL.nav, rail: ['home'] } })
    expect(orderedNav(shell, 'rail', ['home', 'projects', 'add'])).toEqual(['home'])
  })

  it('壳比前端新时多出来的 key 落空，而不是画一格点了就 404 的东西', () => {
    const shell = shellLike({ nav: { ...DEFAULT_SHELL.nav, rail: ['home', '课表', 'add'] } })
    expect(orderedNav(shell, 'rail', ['home', 'projects', 'add'])).toEqual(['home', 'add'])
  })

  it('default 壳下三个面逐格就是今天的样子', () => {
    expect(orderedNav(DEFAULT_SHELL, 'rail', ['home', 'projects', 'add'])).toEqual(['home', 'projects', 'add'])
    expect(orderedNav(DEFAULT_SHELL, 'tabs', ['home', 'workspace', 'inbox'])).toEqual(['home', 'workspace', 'inbox'])
    // 项目页的顺序：资料库摆在项目名下，成员排在菜单第一个（「退出项目」在那一页）。
    expect(orderedNav(DEFAULT_SHELL, 'project', KNOWN)).toEqual([
      'project-library',
      'project-members',
      'project-routines',
    ])
  })
})

describe('termParams: 词表切文案，壳没说就回落 catalog', () => {
  it('壳说了就用壳的', () => {
    const shell = shellLike({ terms: { project: '工作', topic: '议题' } })
    expect(termParams(shell)).toEqual({ project: '工作', topic: '议题' })
  })

  it('壳只说了半个，另一半回落', () => {
    // 落回的**是 catalog 里的词**（`navigation.term.project`），不是一个空串——
    // 空串会把「新建{project}」变成「新建」。
    const shell = shellLike({ terms: { project: '工作' } })
    const terms = termParams(shell)
    expect(terms.project).toBe('工作')
    expect(terms.topic).toBe(t('navigation.term.topic'))
    expect(terms.topic).not.toBe('')
  })

  it('壳什么都没说就是 catalog 的两个词', () => {
    expect(termParams(DEFAULT_SHELL)).toEqual({
      project: t('navigation.term.project'),
      topic: t('navigation.term.topic'),
    })
  })
})

describe('projectPageLayout: 项目名下那一行只有看板和资料库，其余都在项目名菜单里', () => {
  it('项目名下那一行只有看板和资料库', () => {
    // 这一行加一格，频道就往下挪一行；用户整理过不止一次，几个版本后又是一摞入口。
    // 新页面进项目名菜单。改这一条之前先读 .claude/rules/project-sidebar.md。
    const everything = shellLike({ nav: { ...DEFAULT_SHELL.nav, project: [...KNOWN, 'project-skills'] } })
    const { bar } = projectPageLayout(everything, [...KNOWN, 'project-skills'])
    expect(bar).toEqual(['workspace-running', 'project-library'])
  })

  it('default 壳：那一行是看板和资料库，菜单里是其余几页', () => {
    const { bar, menu } = projectPageLayout(DEFAULT_SHELL, KNOWN)
    expect(bar).toEqual(['workspace-running', 'project-library'])
    expect(menu).toEqual(['project-members', 'project-routines'])
  })

  it('壳不摆资料库，资料库就在菜单里', () => {
    const shell = shellLike({ nav: { ...DEFAULT_SHELL.nav, project: ['project-members', 'project-routines'] } })
    const { bar, menu } = projectPageLayout(shell, KNOWN)
    expect(bar).toEqual(['workspace-running'])
    expect(menu).toContain('project-library')
  })

  it('壳写错了 key、或者前端多出一页 —— 那一页在菜单里，不是凭空消失', () => {
    const shells = [
      DEFAULT_SHELL,
      shellLike({ nav: { ...DEFAULT_SHELL.nav, project: ['project-routines', 'no-such-page'] } }),
      shellLike({ nav: { ...DEFAULT_SHELL.nav, project: [] } }),
    ]
    for (const shell of shells) {
      const { bar, menu } = projectPageLayout(shell, KNOWN)
      expect([...bar, ...menu].sort()).toEqual([...KNOWN].sort())
      expect(new Set([...bar, ...menu]).size).toBe(KNOWN.length)
    }
  })

  it('菜单里先按壳的顺序', () => {
    const shell = shellLike({ nav: { ...DEFAULT_SHELL.nav, project: ['project-routines', 'project-members'] } })
    expect(projectPageLayout(shell, KNOWN).menu.slice(0, 2)).toEqual(['project-routines', 'project-members'])
  })
})
