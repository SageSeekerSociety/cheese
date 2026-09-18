import type { Project } from '@/cx_types'
import type { NavItem } from './types'

import { beforeEach, describe, expect, it } from 'vitest'

import { railItems, shortcutTarget, tabItems, workspaceProject } from './destinations'

import { setLocale } from '@/i18n'

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
    for (const list of [railItems(sources(3, 'p1')), tabItems(sources(3, 'p1'))]) {
      for (const item of items(list)) {
        expect(item.to ?? item.action, `${item.title} 既没有地址也没有动作`).toBeTruthy()
      }
    }
  })

  // 浮层上那个 ⌘N 在很长一段时间里指着一个不存在的功能：显示了键，没人绑它。
  describe('⌘N 切到哪一格', () => {
    it('⌘1 是首页，之后依次是项目', () => {
      const rail = railItems(sources(3, 'p1'))
      expect(shortcutTarget(rail, 1)).toBe('/')
      expect(shortcutTarget(rail, 2)).toBe('/projects/p0')
      expect(shortcutTarget(rail, 3)).toBe('/projects/p1')
    })

    // 对不上就交回给浏览器（它自己用这些键切标签页），所以这里必须是 null 而不是
    // 「最后一格」之类的兜底。
    it('没有对应格子的数字不认领', () => {
      const rail = railItems(sources(2, 'p0'))
      expect(shortcutTarget(rail, 9)).toBeNull()
    })

    // 「＋新建项目」是个动作不是目的地：一个手滑就按到的键不该建出东西来。
    it('不会落到「新建项目」上', () => {
      const rail = railItems(sources(2, 'p0'))
      const add = items(rail).find((i) => i.action && !i.to)
      expect(add, '清单里没有那个只有动作的格子，这条用例失去了对象').toBeTruthy()
      expect(add?.shortcut, '「新建项目」被分到了一个数字键').toBeUndefined()
    })
  })

  it('新建项目在两端都到得着', () => {
    const reachable = (list: { type: string }[]) => items(list).some((i) => i.title === '新建项目')
    expect(reachable(railItems(sources(2, 'p0')))).toBe(true)
    // 手机上它不占格，落在工作区那一格里——但项目为零时底栏就是唯一的入口。
    expect(items(tabItems(sources(0))).some((i) => i.action)).toBe(true)
  })

  it('底栏格数不随项目数量增长', () => {
    const counts = [0, 1, 5, 40].map((n) => tabItems(sources(n, n ? 'p0' : null)).length)
    expect(new Set(counts).size).toBe(1)
  })

  it('桌面 rail 每个项目一格', () => {
    const withProjects = items(railItems(sources(4, 'p0')))
    expect(withProjects.filter((i) => i.to?.startsWith('/projects/'))).toHaveLength(4)
  })

  it('工作区那一格落在当前项目上', () => {
    const tab = items(tabItems(sources(3, 'p2'))).find((i) => i.title === '工作区')
    expect(tab?.to).toBe('/projects/p2')
  })

  it('一个项目都没有时，工作区那一格是"建一个"', () => {
    const tab = items(tabItems(sources(0))).find((i) => i.title === '工作区')
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
