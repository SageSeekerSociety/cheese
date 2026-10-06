import type { Project } from '@/cx_types'
import type { Shell } from '@/lib/shell'
import type { MenuAction } from '../menuAction'
import type { NavGenericItem, NavItem } from './types'

import { t } from '@/i18n'
import { orderedNav, termParams } from '@/lib/shell'

// 一级导航在两端是**两份清单**，不是一份清单加两个否定式过滤器。
//
// 过滤器那版的毛病不在于它存在，在于它读起来像一份清单：手机上「首页」被
// `visibleOnMobile: false` 关掉之后，没有任何东西提示它的两个子页必须有人接住
// ——接住了是巧合；「＋新建项目」被同一个开关关掉之后就真的无家可归，手机上
// 因此建不了项目。两份清单从同一批目的地定义里取，"某个目的地只活在一端"
// 就是一句写得出来的事实，而不是过滤器的副作用。
//
// 两端顶层不一样是对的，不是漂移：桌面 rail 是竖列，装得下一个一个平铺的项目
// 实例；底栏只有三格，装不下会随数据增长的东西。要守的不变量只有一条——每个
// 目的地两端都到得着。形态设计见
// docs/plans/2026-08-18-mobile-shell-design.md。
//
// **壳管的是「露出哪几格、什么顺序」**（这一段上面不变量之外的那点自由）：先是
// 壳给的顺序，然后只画这一版前端认得出来的格子。壳比前端新的时候，多出来的 key
// 画不出来，而不是画一格点了就去 404。

// 首页那一格点开是待办；它底下的团队、空间也都算在这一格里（侧栏就是这一格的目录）。
const inHome = (path: string) =>
  path === '/inbox' || path === '/home' || path.startsWith('/teams') || path.startsWith('/spaces')

const HOME: Omit<NavItem, 'title'> = { key: 'Home', type: 'item', to: '/inbox', icon: 'cheese', match: inHome }

// 手机底栏的「首页」：团队和空间的目录（HomeHub）。待办在手机上自己占一格。
const HUB: Omit<NavItem, 'title'> = {
  key: 'Hub',
  type: 'item',
  to: '/home',
  icon: 'mdi-home-outline',
  match: (path) => path === '/home' || path.startsWith('/teams') || path.startsWith('/spaces'),
}

// 手机底栏的「待办」。桌面上没有这一格：待办就是首页那一格点开的那一页。
const INBOX: Omit<NavItem, 'title'> = { key: 'Inbox', type: 'item', to: '/inbox', icon: 'mdi-inbox-outline' }

export interface NavSources {
  projects: Project[]
  /** 工作区那一格落到哪个项目：当前打开的 → 上次打开的 → 第一个。 */
  workspaceProjectId: string | null
  createProject: () => void
  /** 待我处理的件数；还没读到是 0。桌面首页那一格和手机底栏「待办」都画它。 */
  awaitingCount?: number
  /** 这个项目里有几件待我处理；还没读到或没有就是 0。桌面项目格子上画它。 */
  projectAwaitingCount?: (projectId: string) => number
  /**
   * 这个项目上次打开的话题 id；记着的那一个还在的话，格子直接落回那个话题，
   * 否则落到项目首页。第一次打开这个项目时会校验一次，不在了就忘掉。
   */
  projectLastTopic?: (projectId: string) => string | null
  /** 有没有没读的动态（提到你、回复你……）。没有待处理的事时，用一颗小点提醒它。 */
  unreadActivity?: boolean
  /** 右键一个项目格子能做什么。由宿主拼好：里面要用到路由、剪贴板和退出确认框。 */
  projectMenu?: (project: Project) => MenuAction[]
}

/**
 * 工作区那一格落到哪个项目：正开着的 → 上次开过的 → 第一个。
 *
 * 上次那个 id 必须在**这个用户**的项目清单里找得到才算数。存布局的那份
 * localStorage 是整个浏览器一份、不分账号，而项目清单是按 handle 存的
 * (projectCache 的 v2 注释记着同一个坑)——不设这道门，换个账号进来工作区那一格
 * 就指着上一个人的项目，点进去只会 403。
 */
export function workspaceProject(
  projects: Project[],
  openProjectId: string | null,
  lastProjectId: string | null
): string | null {
  if (openProjectId) return openProjectId
  if (lastProjectId && projects.some((p) => p.id === lastProjectId)) return lastProjectId
  return projects[0]?.id ?? null
}

// 工作区那一格落在你上次待的地方，一跳就能进话题——而不是先落到一个项目目录，
// 那等于给每天的主路径加一跳。一个项目都没有的时候它没有落地上下文，此时唯一
// 有意义的动作就是建一个，所以这一格就是那个动作。
function workspace(src: NavSources): NavItem {
  const tab = {
    key: 'Workspace',
    type: 'item' as const,
    title: t('navigation.workspace'),
    icon: 'mdi-folder-multiple-outline',
  }
  return src.workspaceProjectId
    ? { ...tab, to: `/projects/${src.workspaceProjectId}` }
    : { ...tab, action: src.createProject }
}

/** 待办的记号：有待处理的事画件数；没有、但有没读的动态，画一颗小点。 */
function marks(src: NavSources): Pick<NavItem, 'badge' | 'dot'> {
  const badge = src.awaitingCount || 0
  return { badge, dot: !badge && !!src.unreadActivity }
}

/** 项目格子的记号：这个项目里有几件待我处理。和首页那一格同一种画法。 */
function projectMarks(src: NavSources, projectId: string): Pick<NavItem, 'badge'> {
  const badge = src.projectAwaitingCount?.(projectId) ?? 0
  return badge > 0 ? { badge } : {}
}

/**
 * 桌面 rail 的三格长什么样，按 key 摆好等壳来排。
 *
 * 壳只给 key 和顺序，格子本身（图标、落点、动作）永远在这份代码里——这是
 * 「壳里不放代码」那一条的落点：换壳换不掉「＋新建项目」是干什么的。
 */
function railParts(src: NavSources, shell: Shell): Record<string, NavGenericItem[]> {
  const terms = termParams(shell)
  return {
    home: [{ ...HOME, title: t('navigation.home', terms), ...marks(src) }],
    projects: [
      ...(src.projects.length ? [{ key: 'cx-divider', type: 'divider' as const }] : []),
      // Discord 式：一个项目一格方头像（首字母 + 颜色），不是截断的标题。
      ...src.projects.map((p) => {
        const lastTopic = src.projectLastTopic?.(p.id) ?? null
        return {
          key: `cx-${p.id}`,
          type: 'item' as const,
          title: p.name,
          projectId: p.id,
          // 上次打开的那个话题还在，就直接落回它——每天的主路径是「回到昨天那个
          // 房间」，先落到项目首页等于多加一跳。不在了（或没记过）才落到项目首页。
          to: lastTopic ? `/projects/${p.id}/topics/${lastTopic}` : `/projects/${p.id}`,
          // 项目里的每一页（话题、看板、设置）都算站在这一格上。选中框靠这个画：
          // RailItem 自己绑着 aria-current，没有 match 的格子绑上去的是 undefined，
          // 会盖掉链接本来算出的激活态。
          match: (path: string) => path === `/projects/${p.id}` || path.startsWith(`/projects/${p.id}/`),
          menu: src.projectMenu?.(p),
          ...projectMarks(src, p.id),
        }
      }),
    ],
    add: [
      {
        key: 'cx-add',
        type: 'item' as const,
        title: t('navigation.newProject', terms),
        icon: 'mdi-plus',
        add: true,
        action: src.createProject,
      },
    ],
  }
}

/** rail 上一格能带的快捷方式编号上限。App 登记的是 mod+1..9，再多也没键可触发。 */
const MAX_SHORTCUT = 9

/** 桌面左侧 rail：首页（待办、团队、空间都在它的侧栏里）+ 项目实例 + ＋新建项目。 */
export function railItems(src: NavSources, shell: Shell): NavGenericItem[] {
  const parts = railParts(src, shell)
  const items = orderedNav(shell, 'rail', Object.keys(parts)).flatMap((key) => parts[key])
  // ⌘N 是**画出来的位置**，不是某一格固有的属性：壳把项目排到第一格时，⌘1 就该是
  // 那个项目。所以编号发生在排完之后，而不是在建格子的地方写死。
  //
  // 只编到 9：App 只登记 1..9（railShortcut），第 10 格往后拿到的数字没有任何键能触发，浮层上
  // 那句「G 10」是一句谎话。项目多到 9 个以上时，多出来的格子没有快捷方式。
  let n = 0
  return items.map((item) => {
    if (item.type !== 'item' || !item.to || n >= MAX_SHORTCUT) return item
    n += 1
    return { ...item, shortcut: n }
  })
}

/**
 * 第 N 格的快捷键，按「在哪儿跑」分两种（App.vue 登记、RailItem 浮层显示，同一个出处）：
 *
 * - 浏览器里是序列键 `G` 然后 `N`。⌘1–9 是浏览器切标签页的键，抢过来会让人切不回自己
 *   的第 N 个标签页；后台的 `G Q / G D` 是同一个约定。
 * - 桌面 app 没有浏览器标签页，⌘N 不抢任何人的，照旧用它。
 */
export function railShortcut(n: number, desktop: boolean): { shortcut: string; keys: string[] } {
  return desktop ? { shortcut: `mod+${n}`, keys: ['⌘', String(n)] } : { shortcut: `g ${n}`, keys: ['G', String(n)] }
}

/**
 * 按下 ⌘N 该去哪儿，没有对应的格子就是 null。
 *
 * rail 的悬停浮层一直在显示这个键（`shortcut`），而在此之前没有任何地方绑它——
 * 一个指着不存在功能的提示。
 *
 * 只认有地址的格子：「＋新建项目」是个动作而不是目的地，给它一个数字键等于把
 * 一个会建出东西来的操作放在一个手滑就按到的键上。
 */
export function shortcutTarget(items: NavGenericItem[], digit: number): string | null {
  for (const item of items) {
    if (item.type === 'item' && item.shortcut === digit && item.to) return item.to
  }
  return null
}

/** 手机底栏的三格长什么样，按 key 摆好等壳来排。格数固定，不随项目数量增长。 */
function tabParts(src: NavSources, shell: Shell): Record<string, NavItem> {
  const terms = termParams(shell)
  return {
    home: { ...HUB, title: t('navigation.home', terms) },
    workspace: workspace(src),
    inbox: { ...INBOX, title: t('navigation.inbox', terms), ...marks(src) },
  }
}

/** 手机底栏：格数固定，不随项目数量增长。顺序与露出由壳决定。 */
export function tabItems(src: NavSources, shell: Shell): NavItem[] {
  const parts = tabParts(src, shell)
  return orderedNav(shell, 'tabs', Object.keys(parts)).map((key) => parts[key])
}
