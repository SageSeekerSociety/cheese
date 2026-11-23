// 任务页「对话」那一种摆法里，页头右边那一组按钮：一样东西一颗，开着的那几颗是按下的样子。
// 「改动」带着改过几个文件；现场、预览收在那一组最后的 ⋯ 里。

export interface TaskBlockButton {
  /** 工作面板里这一样的名字（`overview`、`changes`、`code`、`terminals`、`site`、`preview`）。 */
  key: string
  label: string
  /** 悬停和读屏念的，比名字多说一点（改了几个文件）。 */
  hint?: string
  icon: string
  /** 它那一块开着。 */
  on: boolean
  /** 按钮角上的数（改了几个文件）；0 不画。 */
  count?: number
  /** 收在 ⋯ 里。 */
  more?: boolean
}
