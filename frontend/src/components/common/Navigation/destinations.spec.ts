import type { Project } from '@/cx_types'
import type { Shell } from '@/lib/shell'
import type { NavItem } from './types'

import { beforeEach, describe, expect, it } from 'vitest'

import { railItems, shortcutTarget, tabItems, workspaceProject } from './destinations'

import { setLocale } from '@/i18n'
import { DEFAULT_SHELL } from '@/lib/shell'

beforeEach(() => setLocale('zh-CN'))

function sources(projectCount: number, workspaceProjectId: string | null = null) {
  const projects = Array.from({ length: projectCount }, (_, i) => ({ id: `p${i}`, name: `项目${i}` })) as Project[]
  return {
    projects,
    workspaceProjectId,
    projectAvatar: (name: string) => `avatar:${name}`,
    createProject: () => {},
  }
}

const items = (list: { type: string }[]) => list.filter((i) => i.type === 'item') as NavItem[]

describe('一级导航的两份清单', () => {
  // 「＋新建项目」在手机上无家可归过一次（底栏把它过滤掉，别处没人接）。
  // 一格既不导航也不触发动作，就是那个 bug 的形状。
  it('每一格都通向某处：要么有地址，要么有动作', () => {
    for (const list of [railItems(sources(3, 'p1'), DEFAULT_SHELL), tabItems(sources(3, 'p1'), DEFAULT_SHELL)]) {
      for (const item of items(list)) {
        expect(item.to ?? item.action, `${item.title} 既没有地址也没有动作`).toBeTruthy()
      }
    }
  })

  // 浮层上那个 ⌘N 在很长一段时间里指着一个不存在的功能：显示了键，没人绑它。
  describe('⌘N 切到哪一格', () => {
    it('⌘1 是首页，之后依次是项目', () => {
      const rail = railItems(sources(3, 'p1'), DEFAULT_SHELL)
      expect(shortcutTarget(rail, 1)).toBe('/')
      expect(shortcutTarget(rail, 2)).toBe('/projects/p0')
      expect(shortcutTarget(rail, 3)).toBe('/projects/p1')
    })

    // 对不上就交回给浏览器（它自己用这些键切标签页），所以这里必须是 null 而不是
    // 「最后一格」之类的兜底。
    it('没有对应格子的数字不认领', () => {
      const rail = railItems(sources(2, 'p0'), DEFAULT_SHELL)
      expect(shortcutTarget(rail, 9)).toBeNull()
    })

    // 「＋新建项目」是个动作不是目的地：一个手滑就按到的键不该建出东西来。
    it('不会落到「新建项目」上', () => {
      const rail = railItems(sources(2, 'p0'), DEFAULT_SHELL)
      const add = items(rail).find((i) => i.action && !i.to)
      expect(add, '清单里没有那个只有动作的格子，这条用例失去了对象').toBeTruthy()
      expect(add?.shortcut, '「新建项目」被分到了一个数字键').toBeUndefined()
    })
  })

  it('新建项目在两端都到得着', () => {
    const reachable = (list: { type: string }[]) => items(list).some((i) => i.title === '新建项目')
    expect(reachable(railItems(sources(2, 'p0'), DEFAULT_SHELL))).toBe(true)
    // 手机上它不占格，落在工作区那一格里——但项目为零时底栏就是唯一的入口。
    expect(items(tabItems(sources(0), DEFAULT_SHELL)).some((i) => i.action)).toBe(true)
  })

  it('底栏格数不随项目数量增长', () => {
    const counts = [0, 1, 5, 40].map((n) => tabItems(sources(n, n ? 'p0' : null), DEFAULT_SHELL).length)
    expect(new Set(counts).size).toBe(1)
  })

  it('桌面 rail 每个项目一格', () => {
    const withProjects = items(railItems(sources(4, 'p0'), DEFAULT_SHELL))
    expect(withProjects.filter((i) => i.to?.startsWith('/projects/'))).toHaveLength(4)
  })

  it('工作区那一格落在当前项目上', () => {
    const tab = items(tabItems(sources(3, 'p2'), DEFAULT_SHELL)).find((i) => i.title === '工作区')
    expect(tab?.to).toBe('/projects/p2')
  })

  it('一个项目都没有时，工作区那一格是"建一个"', () => {
    const tab = items(tabItems(sources(0), DEFAULT_SHELL)).find((i) => i.title === '工作区')
    expect(tab?.to).toBeUndefined()
    expect(tab?.action).toBeTypeOf('function')
  })
})

describe('工作区那一格落到哪个项目', () => {
  const projects = [
    { id: 'a', name: 'A' },
    { id: 'b', name: 'B' },
  ] as Project[]

  it('正开着的项目优先', () => {
    expect(workspaceProject(projects, 'b', 'a')).toBe('b')
  })

  it('没开项目时落回上次那个', () => {
    expect(workspaceProject(projects, null, 'b')).toBe('b')
  })

  // 存布局的 localStorage 不分账号，项目清单才按 handle 存——换个人登录，
  // 上次那个 id 就是别人的项目，点进去只会 403。
  it('上次那个项目不属于这个账号就不认', () => {
    expect(workspaceProject(projects, null, '别人的项目')).toBe('a')
  })

  it('一个项目都没有时没有落点', () => {
    expect(workspaceProject([], null, 'a')).toBeNull()
  })
})

describe('壳决定露出哪几格、什么顺序', () => {
  // 一个**编出来**的壳，故意不叫任何一个真壳的名字：真壳的名字不该出现在组件里
  // （`test_adding_a_shell_touches_no_component` 盯着这条），而这里要验的是「壳说
  // 什么就画什么」，用哪个壳说都一样。
  const mine: Shell = {
    name: 'my-shell',
    home: 'workspace-running',
    nav: {
      rail: ['home', 'projects', 'add'],
      tabs: ['workspace', 'spaces', 'inbox'],
      project: [],
    },
    hidden: [],
    terms: { project: '工作', topic: '议题' },
  }

  it('default 壳下两份清单一个字都没变', () => {
    // 这是整个壳层的验收条件：没声明壳的项目必须和今天逐屏一样。
    expect(items(railItems(sources(3, 'p1'), DEFAULT_SHELL)).map((i) => i.title)).toEqual([
      '首页',
      '项目0',
      '项目1',
      '项目2',
      '新建项目',
    ])
    expect(items(tabItems(sources(3, 'p1'), DEFAULT_SHELL)).map((i) => i.title)).toEqual(['空间', '工作区', '待办'])
  })

  it('底栏按壳给的顺序排', () => {
    expect(items(tabItems(sources(3, 'p0'), mine)).map((i) => i.title)).toEqual(['工作区', '空间', '待办'])
  })

  it('壳没列出来的格子就不画', () => {
    const trimmed: Shell = {
      ...DEFAULT_SHELL,
      nav: { ...DEFAULT_SHELL.nav, tabs: ['spaces', 'workspace'] },
    }
    expect(items(tabItems(sources(1, 'p0'), trimmed)).map((i) => i.title)).toEqual(['空间', '工作区'])
  })

  it('不认识的 key 画不出来，而不是画一格点了就 404', () => {
    const ahead: Shell = {
      ...DEFAULT_SHELL,
      nav: { ...DEFAULT_SHELL.nav, tabs: ['spaces', '课程表', 'inbox'] },
    }
    expect(items(tabItems(sources(1, 'p0'), ahead)).map((i) => i.title)).toEqual(['空间', '待办'])
  })

  it('⌘N 跟着画出来的位置走，不是某一格固有的属性', () => {
    const projectsFirst: Shell = {
      ...DEFAULT_SHELL,
      nav: { ...DEFAULT_SHELL.nav, rail: ['projects', 'home', 'add'] },
    }
    const rail = railItems(sources(2, 'p0'), projectsFirst)
    expect(shortcutTarget(rail, 1)).toBe('/projects/p0')
    expect(shortcutTarget(rail, 3)).toBe('/')
  })

  it('文案按词表切', () => {
    const add = items(railItems(sources(0), mine)).find((i) => i.add)
    expect(add?.title).toBe('新建工作')
  })

  it('default 壳把所有格子都列了出来', () => {
    // rail 和底栏没有「更多」——一格从清单里去掉就是真的到不了。壳在这里只能
    // 重排，不能删；要收起某个平台概念，走 hidden + 「更多」（项目侧栏那条路）。
    expect(DEFAULT_SHELL.nav.rail).toEqual(['home', 'projects', 'add'])
    expect(DEFAULT_SHELL.nav.tabs).toEqual(['spaces', 'workspace', 'inbox'])
  })
})
