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
}
