// 工作面板和外壳布局区之间的接口（`WorkPanel` 的 `layout`）。桌面上工作面板的每一格是布局区
// 里的一块，页面（TopicView）从 layouts/pagePanels.ts 拿到这几样交给面板。
import type { LayoutPanelSignal } from '../layout-area'

export interface WorkLayout {
  /** 这一格在布局区里的落点；还没有就是 undefined。 */
  outlet: (key: string) => HTMLElement | undefined
  /** 此刻在屏幕上的那几格（各格子里在前面的那一个）。 */
  visible: ReadonlySet<string>
  /** 把这一格翻到它那一格的前面（收起的打开，关掉的放回来）。 */
  show: (key: string) => void
  /** 这一格标题栏里面板那一段的落点（`blocks` 时才有）：返回、名字写在这里。 */
  head: (key: string) => HTMLElement | undefined
  /**
   * 任务页的「编辑器」那一种摆法：文件是主角。布局区里只有四块：左栏（`NAVIGATOR`）、中间
   * （`EDITOR_CENTRE`）、中间下面（`EDITOR_BOTTOM`）和对话。中间那一块是一排页签：打开的文件、
   * 改动、概览、预览、从对话里打开的一份产出，各是这一排里的一个页签，内容都落在中间那一块里；
   * 下面那一块是一排终端加「现场」。哪一样落在哪一块、此刻翻到哪一个页签由工作面板定
   * （`components/work/editorTabs.ts`），布局区只知道这四块。
   */
  editor?: boolean
  /**
   * 任务页的「对话」那一种摆法和频道页：每一样是一块，带自己的标题栏（`head`），几块能同时开着。
   * 任务页上「文件」那一块一次一份文件、没有树和页签；终端是一块（`TERMINAL_BLOCK`），一次显示一个；
   * 从对话里打开的一份产出一次一份，换掉上一份。频道页上「支线」「文件」两块先是列表，选中一行在
   * 同一块里显示它，标题栏上有「返回」回到列表；打开的一件任务是一块，✕ 关掉它。
   */
  blocks?: boolean
}

/** 编辑器那一种摆法里左边那一栏（文件 / 搜索 / 改动）。 */
export const NAVIGATOR = 'navigator'
/** 编辑器那一种摆法里中间那一块：一排页签，下面是翻到的那一个。 */
export const EDITOR_CENTRE = 'editor:centre'
/** 编辑器那一种摆法里中间下面那一块：一排终端和「现场」。 */
export const EDITOR_BOTTOM = 'editor:bottom'

/** 「对话」那一种摆法里的终端那一块：一次显示一个终端。 */
export const TERMINAL_BLOCK = 'terminals'

/**
 * 编辑器那一种摆法里一样东西落在哪一块：终端和现场在下面，「在旁边打开」的一份工作文件单开
 * 一格，左栏是左栏，别的（打开的文件、改动、概览、预览、从对话里打开的产出）都是中间那一排里的
 * 一个页签。
 */
export function editorCellOf(key: string): string {
  if (key === NAVIGATOR || key === EDITOR_CENTRE || key === EDITOR_BOTTOM) return key
  if (key === 'site' || key === TERMINAL_BLOCK || key.startsWith('terminal:')) return EDITOR_BOTTOM
  if (key.startsWith('file:work:')) return key
  return EDITOR_CENTRE
}

/** 工作面板一格内容的元素 id：页头「概览」那颗开关用它说自己管着哪几块。 */
export function workPaneId(key: string): string {
  return `work-pane-${key.replace(/[^A-Za-z0-9_-]/g, '-')}`
}

/** 一格页签上要画的：名字、图标、有没有东西、信号。面板算好，页面交给布局区。 */
export interface WorkTabMeta {
  key: string
  title: string
  icon: string
  /** 这一格此刻没东西：字淡一些，照样能点。 */
  faint?: boolean
  hint?: string
  signal?: LayoutPanelSignal
  /** 自由区单击打开的那一格：下一次打开会换掉它，双击留下。 */
  temporary?: boolean
  /** 读者打开时才摆出来、能关的一格（任务的文件）：默认摆法里没有它，关了列在「+」里。 */
  closable?: boolean
  /** 一格出现时单开在这一格旁边（「文件」里「在旁边打开」的那份文件）。 */
  beside?: string
  /** 能开好几格的一种（终端）：总列在「+」里，选它是要页面新开一格，它自己不摆出来。 */
  spawns?: boolean
  /** 一格终端，或开终端的那一种。 */
  terminal?: boolean
}
