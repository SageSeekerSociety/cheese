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
}
