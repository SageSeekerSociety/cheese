import type { Project } from '@/cx_types'

import { describe, expect, it } from 'vitest'

import { DEFAULT_SHELL, orderedNav, projectPagePlan, shellFor, shellOf, termParams } from './shell'

import { t } from '@/i18n'

// 这一版前端还认得的那四格（`TopicSidebar.vue` 的 PROJECT_PAGES）。壳只决定
// 「露哪几格」，认不认得某一格是前端的事。
//
// 它比加壳那天短了：main 的 #1330/#1339 把总览与导出与发布并进了首页，AI 队友
// 也不再是一页，于是那三个 key 从这里消失，default 壳的声明跟着一起收窄。
const KNOWN = ['workspace-running', 'calendar', 'project-library', 'project-members']

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
    const shell = shellLike({ nav: { ...DEFAULT_SHELL.nav, tabs: ['inbox', 'spaces', 'workspace'] } })
    expect(orderedNav(shell, 'tabs', ['spaces', 'workspace', 'inbox'])).toEqual(['inbox', 'spaces', 'workspace'])
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
    expect(orderedNav(DEFAULT_SHELL, 'tabs', ['spaces', 'workspace', 'inbox'])).toEqual([
      'spaces',
      'workspace',
      'inbox',
    ])
    // 侧栏那一面 default 只摆资料库：日历和名册在项目名旁边的 ⋯ 菜单里，
    // 看板就是首页（项目名那一行点下去就到），所以它们不在 nav.project 里。
    expect(orderedNav(DEFAULT_SHELL, 'project', KNOWN)).toEqual(['calendar', 'project-library', 'project-members'])
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

describe('projectPagePlan: 收起是「收起」，永远不是「禁止」', () => {
  it('default 壳下侧栏摆资料库和名册，其余全在「更多」里', () => {
    // 「更多」就是项目名旁边那个 ⋯ 菜单（`TopicSidebar.vue` 的 menuPages）。
    // default 下它是 看板 / 日历 —— 一格都没丢，只是不占那条竖线。名册不在里头：
    // 「退出项目」长在名册页上，收进 ⋯ 就是把「怎么退出」也一起藏了。
    const plan = projectPagePlan(DEFAULT_SHELL, KNOWN, new Set())
    expect(plan.visible).toEqual(['project-library', 'project-members'])
    expect(plan.more).toEqual(['workspace-running', 'calendar'])
  })

  it('hidden 里的页落进「更多」', () => {
    const shell = shellLike({
      nav: { ...DEFAULT_SHELL.nav, project: KNOWN },
      hidden: ['calendar', 'project-members'],
    })
    const plan = projectPagePlan(shell, KNOWN, new Set())
    expect(plan.visible).toEqual(['workspace-running', 'project-library'])
    expect(plan.more).toEqual(['calendar', 'project-members'])
  })

  it('他手动打开过一次，这一页就回到外面（个人级压过壳）', () => {
    const shell = shellLike({ hidden: ['calendar'] })
    expect(projectPagePlan(shell, KNOWN, new Set(['calendar'])).visible).toContain('calendar')
    expect(projectPagePlan(shell, KNOWN, new Set(['calendar'])).more).not.toContain('calendar')
  })

  it('壳写错了 key、或者前端多出一页 —— 那一页在「更多」里，不是凭空消失', () => {
    // 壳只认得两格，剩下两格（包括壳压根没听说过的那个新页）全都收着，但都在。
    const shell = shellLike({
      nav: { ...DEFAULT_SHELL.nav, project: ['workspace-running', 'calendar'] },
      hidden: [],
    })
    const plan = projectPagePlan(shell, KNOWN, new Set())
    expect(plan.visible).toEqual(['workspace-running', 'calendar'])
    expect(plan.more).toEqual(['project-library', 'project-members'])
  })

  it('露出 + 收起 = 认得的全部，一格不多一格不少', () => {
    const shells = [
      DEFAULT_SHELL,
      shellLike({ hidden: ['workspace-running', 'project-members'] }),
      shellLike({ nav: { ...DEFAULT_SHELL.nav, project: ['calendar', 'project-members'] } }),
      shellLike({ hidden: ['calendar'], nav: { ...DEFAULT_SHELL.nav, project: ['calendar'] } }),
    ]
    for (const shell of shells) {
      const revealed = new Set(['calendar'])
      const { visible, more } = projectPagePlan(shell, KNOWN, revealed)
      expect([...visible, ...more].sort()).toEqual([...KNOWN].sort())
      expect(new Set([...visible, ...more]).size).toBe(KNOWN.length)
      // 顺序也还认得出：visible 内部照壳的顺序，more 内部照前端认得的顺序。
      expect(visible).toEqual(orderedNav(shell, 'project', KNOWN).filter((k) => visible.includes(k)))
    }
  })
})
