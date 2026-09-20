import type { Project } from '@/cx_types'
import type { Shell } from '@/lib/shell'
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

const HOME: NavItem = { key: 'Home', type: 'item', title: '首页', to: '/', icon: 'cheese' }

// 这一格装的是首页那一层，落点是空间；小队是它并排的另一半（手机上就是那一行
// 页内分段），所以在 /teams 底下这一格照样亮着——不然人在这一格里翻小队，底栏
// 却整排都是灰的，看起来像已经走出了这个 app 的导航。
const SPACES: NavItem = {
  key: 'Spaces',
  type: 'item',
  title: '空间',
  to: '/spaces',
  icon: 'mdi-view-dashboard',
  match: (path) => path === '/' || path.startsWith('/spaces') || path.startsWith('/teams'),
}

// 「待办」这个词还没定（设计文档 §7 拍板 1），路径和标签都可能再改。
const INBOX: NavItem = { key: 'Inbox', type: 'item', title: '待办', to: '/inbox', icon: 'mdi-inbox-outline' }

export interface NavSources {
  projects: Project[]
  /** 工作区那一格落到哪个项目：当前打开的 → 上次打开的 → 第一个。 */
  workspaceProjectId: string | null
  projectAvatar: (name: string) => string
  createProject: () => void
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

/**
 * 桌面 rail 的三格长什么样，按 key 摆好等壳来排。
 *
 * 壳只给 key 和顺序，格子本身（图标、落点、动作）永远在这份代码里——这是
 * 「壳里不放代码」那一条的落点：换壳换不掉「＋新建项目」是干什么的。
 */
function railParts(src: NavSources, shell: Shell): Record<string, NavGenericItem[]> {
  const terms = termParams(shell)
  return {
    home: [{ ...HOME, title: t('navigation.home', terms) }],
    projects: [
      ...(src.projects.length ? [{ key: 'cx-divider', type: 'divider' as const }] : []),
      // Discord 式：一个项目一格方头像（首字母 + 颜色），不是截断的标题。
      ...src.projects.map((p) => ({
        key: `cx-${p.id}`,
        type: 'item' as const,
        title: p.name,
        projectId: p.id,
        to: `/projects/${p.id}`,
        img: src.projectAvatar(p.name),
      })),
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

/** 桌面左侧 rail：首页（容器，空间/小队在它的侧栏里）+ 项目实例 + ＋新建项目。 */
export function railItems(src: NavSources, shell: Shell): NavGenericItem[] {
  const parts = railParts(src, shell)
  const items = orderedNav(shell, 'rail', Object.keys(parts)).flatMap((key) => parts[key])
  // ⌘N 是**画出来的位置**，不是某一格固有的属性：壳把项目排到第一格时，⌘1 就该是
  // 那个项目。所以编号发生在排完之后，而不是在建格子的地方写死。
  let n = 1
  return items.map((item) => (item.type === 'item' && item.to ? { ...item, shortcut: n++ } : item))
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
    spaces: { ...SPACES, title: t('navigation.spaces', terms) },
    workspace: workspace(src),
    inbox: { ...INBOX, title: t('navigation.inbox', terms) },
  }
}

/** 手机底栏：格数固定，不随项目数量增长。顺序与露出由壳决定。 */
export function tabItems(src: NavSources, shell: Shell): NavItem[] {
  const parts = tabParts(src, shell)
  return orderedNav(shell, 'tabs', Object.keys(parts)).map((key) => parts[key])
}
