/**
 * 壳 (shell)：一套底座 + 不同的壳。
 *
 * 一个壳只能**开关、排序、换词**（home / nav / terms），里面没有一行
 * 逻辑。它是一份声明，不是一段配置——这也是「加第五个壳」不用碰任何组件的原因。
 *
 * **权威在服务端。** 壳的真身在 `backend/app/domain/shell/catalog.py`，随
 * `ProjectOut.shell` 一起下来；这个目录下没有第二份 catalog，所以服务端加一个壳
 * 不需要前端发版。唯一的例外是 `default-shell.json`：**没有任何项目可读的时候**
 * （首页、空间、还没进项目的桌面 rail）导航仍然要画出来，那时得有个答复，而那个
 * 答复只能是 `default`。它是 `CATALOG["default"]` 的一份拷贝，后端有一条用例
 * (`test_the_frontends_fallback_is_the_backend_default`) 盯着两边逐字段相等——
 * 让它们分叉而不响，正是一个副本最坏的结局。
 */
import type { Project } from '@/cx_types'

import DEFAULT_SHELL_JSON from './default-shell.json'

import { t } from '@/i18n'

/** 三个导航面各自的格子与顺序。见 catalog.py 的 `Nav`。 */
export interface ShellNav {
  rail: readonly string[]
  tabs: readonly string[]
  project: readonly string[]
}

export interface Shell {
  name: string
  /** 进项目的第一屏（路由名）；null = 就别动，这个地址本身就是目的地。 */
  home: string | null
  nav: ShellNav
  /** 词表。`{"project": "工作"}` = 这个壳把「项目」叫「工作」。 */
  terms: Readonly<Record<string, string>>
}

/** 没有项目可读时的答复。**只是兜底**，不是第一来源。 */
export const DEFAULT_SHELL: Shell = DEFAULT_SHELL_JSON as Shell

/** 一个项目生效的壳：服务端解析好随项目行下来；没有就用 `default`。 */
export function shellOf(project: Project | null | undefined): Shell {
  const declared = project?.shell as Shell | undefined
  return declared ?? DEFAULT_SHELL
}

/**
 * 清单里这个项目生效的壳；**这个项目不在清单里时返回 null**。
 *
 * null 和 `DEFAULT_SHELL` 是两件事，调用方必须分得开：「还不知道」（清单没到货、
 * 或者这个项目根本不在我的清单里）和「知道，就是 default」。把后者当前者会让人
 * 白等一屏，把前者当后者会把人送到错的第一屏。
 */
export function shellFor(projects: readonly Project[] | undefined, projectId: string | null | undefined): Shell | null {
  if (!projectId) return null
  const found = projects?.find((p) => p.id === projectId)
  return found ? shellOf(found) : null
}

/** 词表里该用什么词。壳没说就回落 catalog 里的中文——`项目` / `话题`。 */
export function termParams(shell: Shell): { project: string; topic: string } {
  return {
    project: shell.terms.project || t('navigation.term.project'),
    topic: shell.terms.topic || t('navigation.term.topic'),
  }
}

/** 壳能命名的导航面。 */
export type ShellSurface = keyof ShellNav

/**
 * 一个导航面按壳的顺序该画哪些格。
 *
 * 只保留**这一版前端认得**的 key：壳比前端新（服务端先发了第五个壳的名字，而这一版
 * 还没有那个页面）时，多出来的 key 应当是画不出来，而不是画一格点了就 404 的东西。
 */
export function orderedNav(shell: Shell, surface: ShellSurface, known: readonly string[]): string[] {
  return shell.nav[surface].filter((key) => known.includes(key))
}

/**
 * 项目名下面那一行能摆的页：除了首页（看板），只有资料库。
 *
 * **这一行不再加东西。** 它每加一格，频道就往下挪一行；几个版本之后这里又是一摞
 * 入口，而用户整理过不止一次（2026-10-05）。新页面一律进点项目名弹出的那个菜单。
 * 守着这条的是 shell.spec.ts 里「项目名下那一行只有看板和资料库」，规则写在
 * `.claude/rules/project-sidebar.md`。
 */
export const PROJECT_BAR_PAGES: readonly string[] = ['project-library']

/**
 * 项目侧栏要画的两组页：项目名下那一行（`bar`），和点项目名弹出的菜单（`menu`）。
 *
 * 那一行是首页加上 `PROJECT_BAR_PAGES` 里壳摆出来了的那几页；壳不摆资料库（老师的
 * 课程壳），资料库就进菜单。**菜单装的是「这一行之外的全部」**：壳写错了 key、前端
 * 多了一个壳没听说过的页面，都落进菜单，不会凭空消失。菜单里先按壳的顺序，壳没提到
 * 的接在后面。
 */
export function projectPageLayout(shell: Shell, known: readonly string[]): { bar: string[]; menu: string[] } {
  const shown = orderedNav(shell, 'project', known)
  const home = shell.home && known.includes(shell.home) ? [shell.home] : []
  const bar = [...home, ...PROJECT_BAR_PAGES.filter((key) => shown.includes(key) && !home.includes(key))]
  const menu = [...shown, ...known.filter((key) => !shown.includes(key))].filter((key) => !bar.includes(key))
  return { bar, menu }
}
