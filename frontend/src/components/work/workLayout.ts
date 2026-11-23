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
  /**
   * 各格一次只显示一格、每一格带自己的标题栏（频道页）：没有页签可点，新打开的一格换掉正显示的
   * 那一格，从别处打开的那几格在标题栏上有「返回」。
   */
  single: boolean
  /** 这一格标题栏里面板那一段的落点（`single` 时才有）：返回、名字写在这里。 */
  head: (key: string) => HTMLElement | undefined
  /**
   * 任务页的「编辑器」那一种摆法：文件是主角。任务的文件树是左边单独的一栏（`NAVIGATOR`），
   * 「文件」那一格只剩打开的文件；概览、现场、改动、预览不常摆着，打开时才进中间，所以进任务
   * 时不替读者挑一格打开。
   */
  editor?: boolean
  /**
   * 任务页的「对话」那一种摆法：每一样是一块，带自己的标题栏（`head`），几块能同时开着。「文件」那
   * 一块一次一份文件、没有树和页签；终端是一块（`TERMINAL_BLOCK`），一次显示一个；从对话里打开的
   * 一份产出一次一份，换掉上一份，标题栏上有「返回」。
   */
  blocks?: boolean
}

/** 编辑器那一种摆法里左边那一栏（文件 / 改动）。 */
export const NAVIGATOR = 'navigator'

/** 「对话」那一种摆法里的终端那一块：一次显示一个终端。 */
export const TERMINAL_BLOCK = 'terminals'

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
  /** 一格终端，或开终端的那一种：编辑器那一种摆法里它们同在中间下面那一格。 */
  terminal?: boolean
}
