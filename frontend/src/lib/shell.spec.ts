import type { Project } from '@/cx_types'

import { describe, expect, it } from 'vitest'

import { t } from '@/i18n'

import { DEFAULT_SHELL, orderedNav, projectPagePlan, shellFor, shellOf, termParams } from './shell'

// 侧栏认得的那七格，顺序就是 catalog 里 default 的顺序（`TopicSidebar.vue` 的
// PROJECT_PAGES）。壳只决定「露哪几格」，认不认得某一格是前端的事。
const KNOWN = [
  'overview',
  'workspace-running',
  'calendar',
  'project-library',
  'project-delivery',
  'project-agents',
  'project-members',
]

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
    expect(orderedNav(shell, 'tabs', ['spaces', 'workspace', 'inbox'])).toEqual([
      'inbox',
      'spaces',
      'workspace',
    ])
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
    expect(orderedNav(DEFAULT_SHELL, 'rail', ['home', 'projects', 'add'])).toEqual([
      'home',
      'projects',
      'add',
    ])
    expect(orderedNav(DEFAULT_SHELL, 'tabs', ['spaces', 'workspace', 'inbox'])).toEqual([
      'spaces',
      'workspace',
      'inbox',
    ])
    expect(orderedNav(DEFAULT_SHELL, 'project', KNOWN)).toEqual(KNOWN)
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
  it('default 壳什么都不收，七格全露、顺序照旧', () => {
    expect(projectPagePlan(DEFAULT_SHELL, KNOWN, new Set())).toEqual({ visible: KNOWN, more: [] })
  })

  it('hidden 里的页落进「更多」', () => {
    const shell = shellLike({ hidden: ['calendar', 'project-delivery'] })
    const plan = projectPagePlan(shell, KNOWN, new Set())
    expect(plan.visible).toEqual(['overview', 'workspace-running', 'project-library', 'project-agents', 'project-members'])
    expect(plan.more).toEqual(['calendar', 'project-delivery'])
  })

  it('他手动打开过一次，这一页就回到外面（个人级压过壳）', () => {
    const shell = shellLike({ hidden: ['calendar'] })
    expect(projectPagePlan(shell, KNOWN, new Set(['calendar'])).visible).toContain('calendar')
    expect(projectPagePlan(shell, KNOWN, new Set(['calendar'])).more).not.toContain('calendar')
  })

  it('壳写错了 key、或者前端多出一页 —— 那一页在「更多」里，不是凭空消失', () => {
    // 壳只认得四格，剩下三格（包括壳压根没听说过的那个新页）全都收着，但都在。
    const shell = shellLike({ nav: { ...DEFAULT_SHELL.nav, project: ['overview', 'calendar'] } })
    const plan = projectPagePlan(shell, KNOWN, new Set())
    expect(plan.visible).toEqual(['overview', 'calendar'])
    expect(plan.more).toEqual(['workspace-running', 'project-library', 'project-delivery', 'project-agents', 'project-members'])
  })

  it('露出 + 收起 = 认得的全部，一格不多一格不少', () => {
    const shells = [
      DEFAULT_SHELL,
      shellLike({ hidden: ['overview', 'project-members'] }),
      shellLike({ nav: { ...DEFAULT_SHELL.nav, project: ['calendar', 'overview'] } }),
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
