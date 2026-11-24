import type { NavTarget } from '@/lib/navTarget'

/**
 * 一项操作：菜单里的一行、底部动作面板里的一行、手机顶栏右边的一颗按钮，都是它。
 *
 * 同一份清单喂给 `AdaptiveMenu`（桌面是 v-menu，手机是 `MobileActionSheet`）和顶栏，
 * 所以一项操作只在一个地方写一次。
 */
export interface MenuAction {
  /** 在这份清单里唯一。 */
  key: string
  label: string
  /** mdi 图标名。手机顶栏只画图标，面板里图标在字前面。不给就只有字：一段都能读懂的
   *  清单（例行事务的 ⋯）不必每行配一个图形，多出来的图形只是噪声。 */
  icon?: string
  /** 删除、移出这类不可撤销的操作：字和图标用 --danger-ink。 */
  danger?: boolean
  /** 这一项前面画一条分隔线：一组新的从这里开始。 */
  divider?: boolean
  /** 分隔线下面那一组的小标题。 */
  section?: string
  /** 行尾写着的此刻的值（「通知」后面的档位）。 */
  value?: string
  /** 名字下面一行小字。 */
  hint?: string
  /** 几项里选一项：给了就画选没选中，选中的打勾。 */
  checked?: boolean
  /** 点开是下一层：桌面是旁边弹出的子菜单，手机面板里换成这一层。 */
  children?: MenuAction[]
  disabled?: boolean
  /** 正在做：顶栏那颗按钮转圈，面板里这一行点不动。 */
  loading?: boolean
  /** 行尾的未读数（「成员」上挂着的私聊未读）。琥珀色：它是未读标记。 */
  badge?: string
  /** 选中后去哪儿。和 onSelect 可以同时给，先跑 onSelect。 */
  to?: NavTarget
  onSelect?: () => void
}
